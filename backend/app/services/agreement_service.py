import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agreements import (
    AGREED_KINDS,
    AgreedKind,
    agreement_items,
    change_request_items,
    removed_keys,
)
from app.change_requests import rewritable_keys, targets_from_json
from app.detailed_design.impact.elements import parse_model
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.model import RequirementRow, RequirementsModel
from app.models.agreement import AgreementRecord
from app.models.baseline import Baseline
from app.models.change_request import ChangeRequest
from app.models.project import Project
from app.repositories.agreement import AgreementRepository
from app.services.detailed_mode import require_detailed


class AgreementService:
    """要素ごとの合意の記録(docs/external_design.md 2.15節)。記録は、合意済みの版(束)の作成・
    「採用にする」・CR の反映から作る(どれも呼ぶ側のトランザクションの中で。commit は呼ぶ側)。
    今の合意の一覧を返す。詳細設計モードだけ。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._records = AgreementRepository(session)

    async def current(self, project: Project) -> list[AgreementRecord]:
        """今の合意(要素ごとの最新の記録。外れたものは除く)を、要素の種類の順(要件 → 利用者と
        使う場面 → 画面 → API → 外部連携)、同じ種類の中は記録の順に返す。詳細設計モードだけ
        (簡易モードは DesignStagesNotAvailableError)。"""
        require_detailed(project, "Agreements")
        order = {kind: i for i, kind in enumerate(AGREED_KINDS)}
        records = await self._records.current(project.id)
        return sorted(records, key=lambda r: (order.get(r.kind, len(order)), r.seq))

    async def record_baseline(
        self,
        baseline: Baseline,
        models: Mapping[str, Mapping[str, Any] | None],
        *,
        recorded_by: uuid.UUID | None,
    ) -> list[AgreementRecord]:
        """束に入った文書(`models`: 文書の種類 → 写した model)の要素をまとめて合意にし、今の
        合意にあって束に無い要素を「外れた」と記録する。"""
        items = agreement_items(
            parse_model(RequirementsModel, models.get("requirements")),
            parse_model(ExternalDesignModel, models.get("external_design")),
        )
        current = await self._records.current(baseline.project_id)
        gone = set(removed_keys((r.key for r in current), items))
        return await self._records.append(
            project_id=baseline.project_id,
            items=items,
            origin="baseline",
            removed=[r for r in current if r.key in gone],
            baseline_id=baseline.id,
            recorded_by=recorded_by,
        )

    async def record_adopt(
        self, project: Project, row: RequirementRow, *, recorded_by: uuid.UUID | None
    ) -> list[AgreementRecord]:
        """「採用にする」で確かめた要件の行を合意にする。簡易ドキュメントモードでは記録しない
        (合意は詳細設計モードの機能のため。採用そのものは両モードでできる)。"""
        if project.mode != "detailed":
            return []
        items = agreement_items(RequirementsModel(purpose="", rows=[row]), None)
        return await self._records.append(
            project_id=project.id, items=items, origin="adopt", recorded_by=recorded_by
        )

    async def record_change_request(
        self,
        project: Project,
        change_request: ChangeRequest,
        models: Mapping[str, Mapping[str, Any] | None],
        *,
        recorded_by: uuid.UUID | None,
    ) -> list[AgreementRecord]:
        """CR を渡した文書の再生成(反映)で、CR で書き換えてよい要素(対象の要件と、保存した予測の
        画面・API)の合意を、作り直した文書(`models`: 文書の種類 → 新しい model)の中身で更新する。
        削除などで無くなった要素は「外れた」と記録する。出どころは CR。簡易ドキュメントモードでは
        記録しない(合意は詳細設計モードの機能のため)。"""
        if project.mode != "detailed":
            return []
        items = agreement_items(
            parse_model(RequirementsModel, models.get("requirements")),
            parse_model(ExternalDesignModel, models.get("external_design")),
        )
        kinds: set[AgreedKind] = {
            kind for doc_type in models for kind in _DOCUMENT_KINDS.get(doc_type, ())
        }
        current = await self._records.current(project.id)
        updated, gone = change_request_items(
            rewritable_keys(
                targets_from_json(change_request.targets), change_request.predicted_impact
            ),
            items,
            (r.key for r in current),
            kinds,
        )
        if not updated and not gone:
            return []
        return await self._records.append(
            project_id=project.id,
            items=updated,
            origin="change_request",
            removed=[r for r in current if r.key in set(gone)],
            change_request_id=change_request.id,
            recorded_by=recorded_by,
        )


# 文書の種類ごとの、合意の対象の要素の種類
_DOCUMENT_KINDS: dict[str, tuple[AgreedKind, ...]] = {
    "requirements": ("requirement", "users"),
    "external_design": ("screen", "api", "integration"),
}
