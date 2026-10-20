import uuid
from collections.abc import Mapping
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.change_requests import INCLUDABLE_STATUSES
from app.change_requests.explanation import REQUIREMENTS_KEY
from app.detailed_design import StageView, stage_key
from app.models.baseline import Baseline
from app.models.change_request import ChangeRequest
from app.models.design_stage import DesignStage
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.repositories.baseline import BaselineRepository, ItemSnapshot
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.generated_document import (
    DOC_TYPES_BY_MODE,
    TEST_SPEC,
    GeneratedDocumentRepository,
)
from app.schemas.baseline import BaselineCreate
from app.services.agreement_service import AgreementService
from app.services.design_stage_service import DesignStageService
from app.services.detailed_mode import require_detailed
from app.services.errors import (
    BaselineDocumentsMissingError,
    BaselineNotFoundError,
    ChangeRequestNotFoundError,
    ChangeRequestNotIncludableError,
    DesignStageGenerationInProgressError,
    DocGenerationInProgressError,
)

logger = structlog.get_logger(__name__)

# 束がそろって持つべき文書(詳細設計モードで生成する文書)
REQUIRED_DOCUMENTS = DOC_TYPES_BY_MODE["detailed"]
# 束に写す文書(上流から)。テスト仕様書は、あれば写す
BASELINE_DOCUMENTS = (*REQUIRED_DOCUMENTS, TEST_SPEC)


class BaselineService:
    """合意済みの版(束)の作成・一覧・取得(docs/external_design.md 2.14節)。

    束は、作った時点の文書(要件定義書・外部設計書・テスト仕様書の表示中の版)と、段階1〜8 の
    承認の写しを写して凍結する。要件定義書・外部設計書の要素は、要素ごとの合意の記録にも残す
    (AgreementService)。承認の写しが無い段階・無い文書も「入っていない」として行を作り、
    そのときの状態を残す。作った後は直さず消さない。検証済みの CR を渡すと、束と CR を結ぶ
    (CR のサービスはこのサービスを使うので、循環を避けて CR はリポジトリで読む)。
    詳細設計モードだけ。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._baselines = BaselineRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._change_requests = ChangeRequestRepository(session)

    async def create(
        self, project: Project, payload: BaselineCreate, *, recorded_by: uuid.UUID | None
    ) -> Baseline:
        """束を作る。条件(この順に確かめる): 詳細設計モード / 文書・段階の生成中でない /
        要件定義書・外部設計書の表示中の版がある / 渡した CR がどれもあり(無ければ
        ChangeRequestNotFoundError)、検証済みで、まだ束に入っていない(違えば
        ChangeRequestNotIncludableError)。未承認・古い段階があっても作れる。"""
        require_detailed(project, "Baselines")
        if project.status == "generating":
            raise DocGenerationInProgressError(
                "設計書を生成しています。完了してから再度お試しください。"
            )
        rows, views = await DesignStageService(self._session).stage_rows(project)
        if any(row.generation_status == "generating" for row in rows.values()):
            raise DesignStageGenerationInProgressError(
                "段階の下書きを生成しています。完了してから再度お試しください。"
            )
        documents = {
            doc_type: await self._documents.get_current(project_id=project.id, doc_type=doc_type)
            for doc_type in BASELINE_DOCUMENTS
        }
        missing = [doc_type for doc_type in REQUIRED_DOCUMENTS if documents[doc_type] is None]
        if missing:
            raise BaselineDocumentsMissingError(f"Documents missing: {', '.join(missing)}")
        change_requests = await self._includable(project, payload.change_request_numbers)

        baseline = await self._baselines.create(
            project_id=project.id,
            number=await self._baselines.next_number(project.id),
            counterpart=payload.counterpart,
            agreed_at=payload.agreed_at,
            memo=payload.memo,
            reason=payload.reason,
            recorded_by=recorded_by,
            items=[*document_snapshots(documents), *stage_snapshots(rows, views)],
        )
        await AgreementService(self._session).record_baseline(
            baseline,
            {doc_type: doc.model if doc else None for doc_type, doc in documents.items()},
            recorded_by=recorded_by,
        )
        for change_request in change_requests:
            change_request.included_baseline = baseline
            await self._change_requests.add_event(
                change_request,
                action="baseline",
                to_status=change_request.status,
                actor_id=recorded_by,
                note=f"合意済みの版 B-{baseline.number} に入れた",
                refs={"baseline_number": baseline.number},
            )
        await self._session.commit()
        logger.info("baseline_created", project_id=str(project.id), number=baseline.number)
        return baseline

    async def _includable(self, project: Project, numbers: list[int]) -> list[ChangeRequest]:
        found: list[ChangeRequest] = []
        for number in numbers:
            change_request = await self._change_requests.get_by_number(
                project_id=project.id, number=number
            )
            if change_request is None:
                raise ChangeRequestNotFoundError(f"Change request CR-{number} not found")
            if change_request.status not in INCLUDABLE_STATUSES:
                raise ChangeRequestNotIncludableError(
                    f"CR-{number} は検証済みでないので、合意済みの版に入れられません。"
                )
            if change_request.included_baseline_id is not None:
                raise ChangeRequestNotIncludableError(
                    f"CR-{number} はもう別の合意済みの版に入っています。"
                )
            found.append(change_request)
        return found

    async def list_for_project(self, project: Project) -> list[Baseline]:
        """プロジェクトの束を新しい順に返す(詳細設計モードだけ)。"""
        require_detailed(project, "Baselines")
        return await self._baselines.list_for_project(project.id)

    async def get(self, project: Project, number: int) -> Baseline:
        """番号の束(写し付き。詳細設計モードだけ)。無ければ BaselineNotFoundError。"""
        require_detailed(project, "Baselines")
        baseline = await self._baselines.get_by_number(project_id=project.id, number=number)
        if baseline is None:
            raise BaselineNotFoundError(f"Baseline B-{number} not found")
        return baseline

    async def requirements(self, project: Project, number: int) -> list[dict[str, Any]]:
        """番号の束の要件定義書の写しにある要件の行(CR の対象を選ぶ画面用)。無ければ
        BaselineNotFoundError。"""
        return baseline_requirement_rows(await self.get(project, number))


def baseline_requirement_rows(baseline: Baseline) -> list[dict[str, Any]]:
    """束の要件定義書の写しにある要件の行(行の順。ID の無い行は除く)。写しが無い・model が
    無ければ空。"""
    item = next((i for i in baseline.items if i.key == REQUIREMENTS_KEY), None)
    if item is None or not item.model:
        return []
    return [row for row in item.model.get("rows", []) if row.get("id")]


def document_snapshots(
    documents: Mapping[str, GeneratedDocument | None],
) -> list[ItemSnapshot]:
    """文書の写し(渡した順)。版が無い文書は「入っていない」。"""
    snapshots = []
    for doc_type, document in documents.items():
        if document is None:
            snapshots.append(ItemSnapshot(key=doc_type, included=False, state="absent"))
            continue
        snapshots.append(
            ItemSnapshot(
                key=doc_type,
                included=True,
                state="present",
                source_version=document.version,
                content=document.content,
                model=document.model,
                generated_model=document.generated_model,
            )
        )
    return snapshots


def stage_snapshots(
    rows: Mapping[int, DesignStage], views: Mapping[int, StageView]
) -> list[ItemSnapshot]:
    """段階の写し(段階の番号の順)。承認の写しがあれば、今の状態(承認の後に編集した・古いも)と
    一緒に写す。承認したことの無い段階は「入っていない」。"""
    snapshots = []
    for stage in sorted(views):
        row = rows.get(stage)
        state = views[stage].state
        if row is None or row.approved_model is None:
            snapshots.append(ItemSnapshot(key=stage_key(stage), included=False, state=state))
            continue
        snapshots.append(
            ItemSnapshot(
                key=stage_key(stage),
                included=True,
                state=state,
                source_version=row.approved_version,
                model=row.approved_model,
            )
        )
    return snapshots
