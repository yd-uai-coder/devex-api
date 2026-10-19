import uuid
from collections.abc import Mapping

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.detailed_design import StageView, stage_key
from app.models.baseline import Baseline
from app.models.design_stage import DesignStage
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.repositories.baseline import BaselineRepository, ItemSnapshot
from app.repositories.generated_document import (
    DOC_TYPES_BY_MODE,
    TEST_SPEC,
    GeneratedDocumentRepository,
)
from app.schemas.baseline import BaselineCreate
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    BaselineDocumentsMissingError,
    BaselineNotFoundError,
    DesignStageGenerationInProgressError,
    DesignStagesNotAvailableError,
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
    承認の写しを写して凍結する。承認の写しが無い段階・無い文書も「入っていない」として行を作り、
    そのときの状態を残す。作った後は直さず消さない。詳細設計モードだけ。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._baselines = BaselineRepository(session)
        self._documents = GeneratedDocumentRepository(session)

    async def create(
        self, project: Project, payload: BaselineCreate, *, recorded_by: uuid.UUID | None
    ) -> Baseline:
        """束を作る。条件(この順に確かめる): 詳細設計モード / 文書・段階の生成中でない /
        要件定義書・外部設計書の表示中の版がある。未承認・古い段階があっても作れる。"""
        if project.mode != "detailed":
            raise DesignStagesNotAvailableError(
                f"Baselines are not available for project {project.id} (mode={project.mode})"
            )
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
        await self._session.commit()
        logger.info("baseline_created", project_id=str(project.id), number=baseline.number)
        return baseline

    async def list_for_project(self, project: Project) -> list[Baseline]:
        """プロジェクトの束を新しい順に返す。"""
        return await self._baselines.list_for_project(project.id)

    async def get(self, project: Project, number: int) -> Baseline:
        """番号の束(写し付き)。無ければ BaselineNotFoundError。"""
        baseline = await self._baselines.get_by_number(project_id=project.id, number=number)
        if baseline is None:
            raise BaselineNotFoundError(f"Baseline B-{number} not found")
        return baseline


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
