import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.detailed_design import (
    STAGE_INPUTS,
    StageRecord,
    StageView,
    can_approve,
    current_inputs,
    derive_states,
)
from app.models.design_stage import DesignStage
from app.models.project import Project
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.design_stage import DesignStageRead
from app.services.errors import (
    DesignStageLockedError,
    DesignStageNotApprovableError,
    DesignStageNotFoundError,
    DesignStagesNotAvailableError,
    DesignStageVersionConflictError,
)

logger = structlog.get_logger(__name__)

# 人が保存した段階の状態('draft'はAIの下書きだけが作る。UML図のSTATUS_AFTER_EDITと同じ考え方)
STATUS_AFTER_EDIT = "reviewing"


class DesignStageService:
    """詳細設計モードの段階の取得・保存・承認を担当するサービス(全段階に共通の部分)。

    段階ごとの中身(AIの下書き・検証)は各段階の実装Phase(16〜20)で足す。ここで扱う承認の
    条件は、段階に共通の3つだけ: 段階が開いている(入力がそろっている)、versionが一致する、
    承認できる状態で内容が空でない。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._stages = DesignStageRepository(session)
        self._documents = GeneratedDocumentRepository(session)

    async def list_stages(self, project: Project) -> list[DesignStageRead]:
        """段階1〜7の状態を返す(未着手の段階も含む)。"""
        _ensure_detailed(project)
        rows, views = await self._load(project.id)
        return [_to_read(views[stage], rows.get(stage)) for stage in STAGE_INPUTS]

    async def save(
        self, project: Project, *, stage: int, expected_version: int | None, model: dict
    ) -> DesignStageRead:
        """段階の内容を人の編集として保存する。承認済みの段階を保存すると承認をやり直す
        (`reviewing`へ戻し、versionを増やす)。未着手の段階は、初めての保存で行を作る。"""
        _ensure_detailed(project)
        rows, views = await self._load(project.id)
        _ensure_open(views[stage])
        row = rows.get(stage)
        if row is None:
            if expected_version is not None:
                raise DesignStageVersionConflictError(f"Stage {stage} does not exist yet")
            row = await self._stages.create(
                project_id=project.id, stage=stage, model=model, status=STATUS_AFTER_EDIT
            )
        else:
            _ensure_version(row, expected_version)
            row.model = model
            row.status = STATUS_AFTER_EDIT
            row.version += 1
        await self._session.commit()
        await self._session.refresh(row)
        return await self._read_one(project.id, stage)

    async def approve(
        self, project: Project, *, stage: int, expected_version: int
    ) -> DesignStageRead:
        """段階を承認する。承認したときの入力の版を`input_fingerprint`に記録する(後で前の段階・
        文書が変わったら「古い」と判定するため)。versionは増やさない。

        条件(この順に確かめる): 行がある / versionが一致する / 段階が開いている /
        承認できる状態(下書き・レビュー中・古い) / 内容が空でない。"""
        _ensure_detailed(project)
        rows, views = await self._load(project.id)
        row = rows.get(stage)
        if row is None:
            raise DesignStageNotFoundError(f"Stage {stage} has not been started")
        _ensure_version(row, expected_version)
        view = views[stage]
        _ensure_open(view)
        if not can_approve(view.state):
            raise DesignStageNotApprovableError(f"Stage {stage} is already approved")
        if not row.model:
            raise DesignStageNotApprovableError(f"Stage {stage} has no content")

        row.status = "approved"
        row.approved_version = row.version
        row.input_fingerprint = current_inputs(
            stage,
            approved_stage_versions=_approved_versions(rows, views),
            doc_versions=await self._doc_versions(project.id),
        )
        await self._session.commit()
        await self._session.refresh(row)
        logger.info("design_stage_approved", project_id=str(project.id), stage=stage)
        return await self._read_one(project.id, stage)

    async def _load(
        self, project_id: uuid.UUID
    ) -> tuple[dict[int, DesignStage], dict[int, StageView]]:
        rows = {row.stage: row for row in await self._stages.list_for_project(project_id)}
        records = {stage: _to_record(row) for stage, row in rows.items()}
        views = derive_states(records, await self._doc_versions(project_id))
        return rows, views

    async def _read_one(self, project_id: uuid.UUID, stage: int) -> DesignStageRead:
        rows, views = await self._load(project_id)
        return _to_read(views[stage], rows.get(stage))

    async def _doc_versions(self, project_id: uuid.UUID) -> dict[str, int | None]:
        """段階の入力になる文書の、表示中(is_current)の版。"""
        doc_types = {d for inputs in STAGE_INPUTS.values() for d in inputs.documents}
        versions: dict[str, int | None] = {}
        for doc_type in sorted(doc_types):
            document = await self._documents.get_current(project_id=project_id, doc_type=doc_type)
            versions[doc_type] = document.version if document is not None else None
        return versions


def _ensure_detailed(project: Project) -> None:
    if project.mode != "detailed":
        raise DesignStagesNotAvailableError(
            f"Project {project.id} is not in detailed design mode (mode={project.mode})"
        )


def _ensure_open(view: StageView) -> None:
    if not view.is_open:
        raise DesignStageLockedError(
            f"Stage {view.stage} is locked: missing {', '.join(view.missing_inputs)}"
        )


def _ensure_version(row: DesignStage, expected_version: int | None) -> None:
    if expected_version != row.version:
        raise DesignStageVersionConflictError(
            f"Stage {row.stage} version mismatch "
            f"(expected {expected_version}, actual {row.version})"
        )


def _to_record(row: DesignStage) -> StageRecord:
    return StageRecord(
        status=row.status,  # type: ignore[arg-type]
        version=row.version,
        approved_version=row.approved_version,
        input_fingerprint=row.input_fingerprint,
    )


def _approved_versions(
    rows: dict[int, DesignStage], views: dict[int, StageView]
) -> dict[int, int | None]:
    """承認済み(古くない)段階の承認した版。derive_statesの「今の値」と同じ規則。"""
    return {
        stage: row.approved_version
        for stage, row in rows.items()
        if views[stage].state == "approved"
    }


def _to_read(view: StageView, row: DesignStage | None) -> DesignStageRead:
    return DesignStageRead(
        stage=view.stage,
        state=view.state,
        is_open=view.is_open,
        missing_inputs=list(view.missing_inputs),
        version=row.version if row is not None else None,
        approved_version=row.approved_version if row is not None else None,
        model=row.model if row is not None else None,
        updated_at=row.updated_at if row is not None else None,
    )
