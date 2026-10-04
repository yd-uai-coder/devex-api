import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.detailed_design import (
    DATA_MODEL_STAGE,
    ER_SUBJECT,
    STAGE_INPUTS,
    Fingerprint,
    StageRecord,
    StageView,
    can_approve,
    confirm_drafts,
    current_inputs,
    derive_states,
    dfd_accesses,
    er_table_names,
    table_key,
    tables_without_primary_key,
)
from app.detailed_design.validation import (
    DfdDiagramSummary,
    ErDiagramSummary,
    StageSources,
    has_errors,
    selected_dfd_accesses,
    validate_stage,
)
from app.models.design_stage import DesignStage
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.models.uml_diagram import UmlDiagram
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.schemas.design_stage import DesignStageRead, DfdAccessRead, StageIssueRead
from app.services.errors import (
    DesignStageGenerationInProgressError,
    DesignStageInvalidError,
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

    承認の条件は、全段階に共通の3つ(段階が開いている(入力がそろっている)、versionが一致する、
    承認できる状態で内容が空でない)と、段階ごとの検証(app/detailed_design/validation.py)で
    エラーが無いこと(Phase 16)。AIの下書きの生成はdesign_stage_generation_service.pyが担い、
    生成中の段階はここで保存・承認を断る。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._stages = DesignStageRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._diagrams = UmlDiagramRepository(session)

    async def list_stages(self, project: Project) -> list[DesignStageRead]:
        """段階1〜7の状態を返す(未着手の段階も含む)。"""
        _ensure_detailed(project)
        rows, views, documents = await self._load(project.id)
        sources = await self._sources(project.id, rows, views, documents)
        return [_to_read(views[stage], rows.get(stage), sources) for stage in STAGE_INPUTS]

    async def stage_view(
        self, project: Project, stage: int
    ) -> tuple[DesignStage | None, StageView, StageSources]:
        """段階1つ分の行(未着手ならNone)・状態・入力(文書の本文・承認済みの段階の内容・DFD の
        要約)を返す(下書きの生成が使う)。"""
        _ensure_detailed(project)
        rows, views, documents = await self._load(project.id)
        sources = await self._sources(project.id, rows, views, documents)
        return rows.get(stage), views[stage], sources

    async def current_fingerprint(self, project: Project, stage: int) -> Fingerprint:
        """段階が今入力にしているものの版(下書きの生成時に記録する。承認時と同じ規則)。"""
        rows, views, documents = await self._load(project.id)
        return current_inputs(
            stage,
            approved_stage_versions=_approved_versions(rows, views),
            doc_versions=_doc_versions(documents),
        )

    async def read(self, project_id: uuid.UUID, stage: int) -> DesignStageRead:
        """段階1つ分の今の状態を返す。"""
        return await self._read_one(project_id, stage)

    async def save(
        self, project: Project, *, stage: int, expected_version: int | None, model: dict
    ) -> DesignStageRead:
        """段階の内容を人の編集として保存する。承認済みの段階を保存すると承認をやり直す
        (`reviewing`へ戻し、versionを増やす)。未着手の段階は、初めての保存で行を作る。"""
        _ensure_detailed(project)
        rows, views, _ = await self._load(project.id)
        _ensure_open(views[stage])
        row = rows.get(stage)
        if row is not None:
            _ensure_not_generating(row)
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

    async def mark_edited(self, project_id: uuid.UUID, stage: int) -> bool:
        """段階の内容のうち、段階の外に正本を持つもの(段階2の DFD・データ辞書)が直されたとき、
        承認済みの段階を人の編集と同じ扱いで差し戻す(`reviewing`へ戻し、versionを増やす)。
        差し戻したらTrue。

        段階の`model`の保存を経ずに内容が変わっても、承認をやり直させ、後ろの段階に「古い」を
        伝えるため(後ろの段階は、承認した版の番号で陳腐化を判定する)。承認済みでない段階は何も
        しない。行の無いプロジェクト(簡易ドキュメントモード)も何もしない。呼び出し元の保存と同じ
        トランザクションで使うため、commitしない(Phase 17)。"""
        row = await self._stages.get(project_id=project_id, stage=stage)
        if row is None or row.status != "approved":
            return False
        row.status = STATUS_AFTER_EDIT
        row.version += 1
        logger.info("design_stage_reopened", project_id=str(project_id), stage=stage)
        return True

    async def approve(
        self, project: Project, *, stage: int, expected_version: int
    ) -> DesignStageRead:
        """段階を承認する。承認したときの入力の版を`input_fingerprint`に記録する(後で前の段階・
        文書が変わったら「古い」と判定するため)。versionは増やさない。

        条件(この順に確かめる): 行がある / versionが一致する / 段階が開いている /
        承認できる状態(下書き・レビュー中・古い) / 内容が空でない / 段階ごとの検証でエラーが無い。
        生成中の段階は承認しない(生成の結果で内容が変わるため)。"""
        _ensure_detailed(project)
        rows, views, documents = await self._load(project.id)
        row = rows.get(stage)
        if row is None:
            raise DesignStageNotFoundError(f"Stage {stage} has not been started")
        _ensure_not_generating(row)
        _ensure_version(row, expected_version)
        view = views[stage]
        _ensure_open(view)
        if not can_approve(view.state):
            raise DesignStageNotApprovableError(f"Stage {stage} is already approved")
        if not row.model:
            raise DesignStageNotApprovableError(f"Stage {stage} has no content")
        sources = await self._sources(project.id, rows, views, documents)
        if has_errors(validate_stage(stage, row.model, sources)):
            raise DesignStageInvalidError(f"Stage {stage} has validation errors")

        if stage == DATA_MODEL_STAGE:
            # 承認 = 人の確定なので、CRUD 図に残っている AI の下書きの印を外す(Phase 18)
            row.model = confirm_drafts(row.model)
        row.status = "approved"
        row.approved_version = row.version
        row.input_fingerprint = current_inputs(
            stage,
            approved_stage_versions=_approved_versions(rows, views),
            doc_versions=_doc_versions(documents),
        )
        await self._session.commit()
        await self._session.refresh(row)
        logger.info("design_stage_approved", project_id=str(project.id), stage=stage)
        return await self._read_one(project.id, stage)

    async def _load(
        self, project_id: uuid.UUID
    ) -> tuple[
        dict[int, DesignStage], dict[int, StageView], dict[str, GeneratedDocument | None]
    ]:
        rows = {row.stage: row for row in await self._stages.list_for_project(project_id)}
        records = {stage: _to_record(row) for stage, row in rows.items()}
        documents = await self._current_documents(project_id)
        views = derive_states(records, _doc_versions(documents))
        return rows, views, documents

    async def _read_one(self, project_id: uuid.UUID, stage: int) -> DesignStageRead:
        rows, views, documents = await self._load(project_id)
        sources = await self._sources(project_id, rows, views, documents)
        return _to_read(views[stage], rows.get(stage), sources)

    async def _sources(
        self,
        project_id: uuid.UUID,
        rows: dict[int, DesignStage],
        views: dict[int, StageView],
        documents: dict[str, GeneratedDocument | None],
    ) -> StageSources:
        """段階ごとの検証・下書きの生成に渡す入力。

        - 文書: 入力になる文書の表示中の版の本文。
        - 段階: 承認済み(古くない)段階の内容。後ろの段階は、承認済みの前の段階だけを入力にする。
        - DFD: 機能グループの DFD(段階2)の要約と、線から読み取った R/W(段階3)。詳細設計モードでは
          DFD はすべて段階2のもの。
        - ER: 段階3の ER(全体1枚)の要約。
        """
        diagrams = await self._diagrams.list_by_notation(project_id, "dfd")
        er = await self._diagrams.get_by_subject(
            project_id=project_id, notation="er", subject=ER_SUBJECT
        )
        return StageSources(
            documents={
                doc_type: document.content
                for doc_type, document in documents.items()
                if document is not None
            },
            stages={
                stage: row.model
                for stage, row in rows.items()
                if views[stage].state == "approved" and row.model
            },
            dfd_diagrams={diagram.subject: _dfd_summary(diagram) for diagram in diagrams},
            er_diagram=_er_summary(er) if er is not None else None,
        )

    async def _current_documents(
        self, project_id: uuid.UUID
    ) -> dict[str, GeneratedDocument | None]:
        """段階の入力になる文書の、表示中(is_current)の版(まだ無ければNone)。"""
        doc_types = {d for inputs in STAGE_INPUTS.values() for d in inputs.documents}
        return {
            doc_type: await self._documents.get_current(project_id=project_id, doc_type=doc_type)
            for doc_type in sorted(doc_types)
        }


def _doc_versions(documents: dict[str, GeneratedDocument | None]) -> dict[str, int | None]:
    return {
        doc_type: document.version if document is not None else None
        for doc_type, document in documents.items()
    }


def _dfd_summary(diagram: UmlDiagram) -> DfdDiagramSummary:
    elements = (diagram.semantic_model or {}).get("elements", [])
    return DfdDiagramSummary(
        status=diagram.status,
        generation_status=diagram.generation_status,
        process_ids=tuple(
            str(e.get("id")) for e in elements if e.get("element_type") == "process"
        ),
        accesses=tuple(dfd_accesses([diagram.semantic_model or {}])),
    )


def _er_summary(diagram: UmlDiagram) -> ErDiagramSummary:
    return ErDiagramSummary(
        status=diagram.status,
        generation_status=diagram.generation_status,
        tables=tuple(er_table_names(diagram.semantic_model)),
        tables_without_pk=tuple(tables_without_primary_key(diagram.semantic_model)),
    )


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


def _ensure_not_generating(row: DesignStage) -> None:
    if row.generation_status == "generating":
        raise DesignStageGenerationInProgressError(
            f"Stage {row.stage} draft is being generated"
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


def _to_read(view: StageView, row: DesignStage | None, sources: StageSources) -> DesignStageRead:
    issues = validate_stage(view.stage, row.model, sources) if row is not None else []
    return DesignStageRead(
        stage=view.stage,
        state=view.state,
        is_open=view.is_open,
        missing_inputs=list(view.missing_inputs),
        version=row.version if row is not None else None,
        approved_version=row.approved_version if row is not None else None,
        model=row.model if row is not None else None,
        updated_at=row.updated_at if row is not None else None,
        generation_status=row.generation_status if row is not None else None,  # type: ignore[arg-type]
        generation_error=row.generation_error if row is not None else None,
        issues=[StageIssueRead.model_validate(issue, from_attributes=True) for issue in issues],
        dfd_accesses=_dfd_access_reads(sources) if view.stage == DATA_MODEL_STAGE else [],
    )


def _dfd_access_reads(sources: StageSources) -> list[DfdAccessRead]:
    """段階3の画面に渡す DFD の R/W。テーブル名は ER の名前に戻す(画面は ER の列の名前で引く)。"""
    names = {table_key(t): t for t in (sources.er_diagram.tables if sources.er_diagram else ())}
    return [
        DfdAccessRead(
            function_id=a.function_id, table=names.get(a.table, a.table), kind=a.kind
        )
        for a in selected_dfd_accesses(sources)
    ]
