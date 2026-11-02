import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agreements import (
    AGREED_KINDS,
    AgreedKind,
    ChangeRequestRef,
    CurrentAgreement,
    DocumentAgreementStatus,
    ElementHistory,
    RecordFacts,
    VersionFacts,
    agreement_items,
    change_request_items,
    element_history,
    element_states,
    kind_of,
    origin_label,
    removed_keys,
    restored_elements,
)
from app.change_requests import (
    RecordFact,
    predicted_document_keys,
    rewritable_keys,
    targets_from_json,
)
from app.detailed_design.impact.elements import parse_model
from app.documents.external_design.model import ExternalDesignModel
from app.documents.requirements.model import RequirementRow, RequirementsModel, UserScene
from app.models.agreement import AgreementRecord
from app.models.baseline import Baseline
from app.models.change_request import ChangeRequest
from app.models.project import Project
from app.repositories.agreement import AgreementRepository
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.services.detailed_mode import require_detailed
from app.services.errors import AgreementKeyInvalidError


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

    async def record_facts(self, project_id: uuid.UUID) -> list[RecordFact]:
        """プロジェクトの合意の記録を、競合と束の鮮度の判定の材料にして返す(記録の順)。"""
        return [_record_fact(r) for r in await self._records.list_for_project(project_id)]

    async def history(self, project: Project, key: str) -> ElementHistory:
        """要素1つの履歴(合意の記録・合意の形へ戻した版〈巡回で消えた版の写しを含む〉・関わる
        CR)。詳細設計モードだけ
        (簡易モードは DesignStagesNotAvailableError)。合意の対象でないキーは
        AgreementKeyInvalidError。記録も CR も無いキーは空の履歴(まだ合意していない要素)。"""
        require_detailed(project, "Agreements")
        if kind_of(key) is None:
            raise AgreementKeyInvalidError(f"合意の対象でない要素のキーです: {key}")
        records = await self._records.list_for_key(project.id, key)
        change_requests = await ChangeRequestRepository(self._session).list_for_project(project.id)
        numbers = {cr.id: cr.number for cr in change_requests}
        documents = GeneratedDocumentRepository(self._session)
        versions = await documents.list_protected(project.id, tuple(_DOCUMENT_KINDS))
        pruned = await documents.list_pruned_protections(project.id, tuple(_DOCUMENT_KINDS))
        return element_history(
            key,
            [_record_facts(r) for r in records],
            [
                VersionFacts(
                    doc_type=d.doc_type,
                    version=d.version,
                    created_at=d.created_at,
                    protection=d.protection or [],
                    change_request_number=numbers.get(d.change_request_id)
                    if d.change_request_id
                    else None,
                )
                for d in versions
            ]
            + [
                VersionFacts(
                    doc_type=p.doc_type,
                    version=p.version,
                    created_at=p.created_at,
                    protection=p.protection,
                )
                for p in pruned
            ],
            [
                ChangeRequestRef(
                    number=cr.number,
                    title=cr.title,
                    status=cr.status,
                    targets=targets_from_json(cr.targets),
                    predicted_keys=predicted_document_keys(cr.predicted_impact),
                )
                for cr in change_requests
            ],
        )

    async def status(self, project: Project, doc_type: str) -> DocumentAgreementStatus:
        """要件定義書・外部設計書(`doc_type`)の表示中の版の、要素ごとの合意の状態と、その版で
        合意の形へ戻した要素。文書が無ければ空(版は None)。model の無い以前の形式の版は要素が
        無く、合意済みの要素はすべて「今の版に無い」になる。詳細設計モードだけ(簡易モードは
        DesignStagesNotAvailableError)。"""
        require_detailed(project, "Agreements")
        kinds = _DOCUMENT_KINDS[doc_type]
        document = await GeneratedDocumentRepository(self._session).get_current(
            project_id=project.id, doc_type=doc_type
        )
        if document is None:
            return DocumentAgreementStatus(doc_type, None, None, (), ())
        requirements = doc_type == "requirements"
        items = agreement_items(
            parse_model(RequirementsModel, document.model) if requirements else None,
            parse_model(ExternalDesignModel, document.model) if not requirements else None,
        )
        agreements = [
            _current_agreement(r) for r in await self.current(project) if r.kind in kinds
        ]
        cr_number = None
        if document.change_request_id is not None:
            change_request = await ChangeRequestRepository(self._session).get_by_id(
                document.change_request_id
            )
            cr_number = change_request.number if change_request is not None else None
        return DocumentAgreementStatus(
            doc_type,
            document.version,
            cr_number,
            tuple(element_states(items, agreements, kinds)),
            tuple(restored_elements(document.protection or [], items, agreements)),
        )

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

    async def record_adopt_users(
        self, project: Project, users: list[UserScene], *, recorded_by: uuid.UUID | None
    ) -> list[AgreementRecord]:
        """確認事項の一覧で推測の利用者を採用にしたとき、利用者と使う場面(1.2)の全体を合意に
        する(1.2 は全体で1つの要素のため)。簡易ドキュメントモードでは記録しない。"""
        if project.mode != "detailed":
            return []
        items = agreement_items(RequirementsModel(purpose="", users=users), None)
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


def _record_fact(record: AgreementRecord) -> RecordFact:
    """記録の行から、競合と束の鮮度の判定の材料を作る(出どころの CR は読み込み済みであること)。"""
    return RecordFact(
        seq=record.seq,
        key=record.key,
        origin=record.origin,
        baseline_id=record.baseline_id,
        change_request_number=(
            record.change_request.number if record.change_request is not None else None
        ),
        content=record.content,
    )


def _record_facts(record: AgreementRecord) -> RecordFacts:
    """記録の行から履歴の材料を作る(出どころの束・CR は読み込み済みであること)。"""
    return RecordFacts(
        seq=record.seq,
        created_at=record.created_at,
        label=record.label,
        content=record.content,
        origin=record.origin,
        origin_label=origin_label(
            record.origin,  # type: ignore[arg-type]
            baseline_number=record.baseline.number if record.baseline is not None else None,
            cr_number=record.change_request.number if record.change_request is not None else None,
        ),
        recorded_by=record.recorded_by,
    )


def _current_agreement(record: AgreementRecord) -> CurrentAgreement:
    """今の合意の記録の行から状態の材料を作る(出どころの束・CR は読み込み済みであること)。"""
    baseline_number = record.baseline.number if record.baseline is not None else None
    cr_number = record.change_request.number if record.change_request is not None else None
    return CurrentAgreement(
        key=record.key,
        kind=record.kind,  # type: ignore[arg-type]
        label=record.label,
        content=record.content or {},
        origin_label=origin_label(
            record.origin,  # type: ignore[arg-type]
            baseline_number=baseline_number,
            cr_number=cr_number,
        ),
        baseline_number=baseline_number,
        change_request_number=cr_number,
    )


# 文書の種類ごとの、合意の対象の要素の種類
_DOCUMENT_KINDS: dict[str, tuple[AgreedKind, ...]] = {
    "requirements": ("requirement", "users"),
    "external_design": ("screen", "api", "integration"),
}
