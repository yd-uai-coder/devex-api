import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.detailed_design import (
    DATA_MODEL_STAGE,
    ER_SUBJECT,
    PLAN_STAGE,
    PROCEDURE_DOC_STAGE,
    PROCEDURE_STAGE,
    STRUCTURE_STAGE,
    STRUCTURE_SUBJECT,
    Fingerprint,
    ModuleListModel,
    PlanModel,
    ProcedureDocModel,
    ProcedureModel,
    StageRecord,
    StageView,
    assign_unit_keys,
    can_approve,
    component_element_layers,
    component_layers,
    confirm_drafts,
    current_inputs,
    derive_states,
    dfd_accesses,
    er_table_definitions,
    er_table_names,
    find_unit,
    module_dependencies,
    relabel_units,
    table_key,
    tables_without_primary_key,
)
from app.detailed_design.impact import (
    DOCUMENT_STAGE,
    REGENERATION_STAGES,
    DesignModels,
    Element,
    SimpleModels,
    StageImpact,
    blocking_marks,
    build_elements,
    build_simple_elements,
    diff_elements,
    doc_source,
    group_by_stage,
    marks_for,
    record,
    set_reviewed,
    stage_source,
)
from app.detailed_design.procedure_basis import ProcedureBasis, procedure_basis
from app.detailed_design.procedure_output import (
    count_by_level,
    procedure_output_source,
    to_ai_markdown,
    unit_findings,
)
from app.detailed_design.sequence import to_sequence
from app.detailed_design.sequence_svg import to_sequence_svg
from app.detailed_design.stages import StageInputs, stage_inputs
from app.detailed_design.validation import (
    ComponentDiagramSummary,
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
from app.models.review import ReviewFinding
from app.models.uml_diagram import UmlDiagram
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.review import ReviewRunRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.schemas.design_stage import (
    DesignImpactRead,
    DesignRefRead,
    DesignStageRead,
    DfdAccessRead,
    ElementChangeRead,
    GenerationFindingRead,
    ImpactMarkRead,
    RegenerationTargetRead,
    SequenceIssueRead,
    SequenceRead,
    StageImpactRead,
    StageIssueRead,
    UnitAiMarkdownRead,
    UnitContextRead,
)
from app.services.errors import (
    DesignProcedureNotFoundError,
    DesignStageGenerationInProgressError,
    DesignStageInvalidError,
    DesignStageLockedError,
    DesignStageNotApprovableError,
    DesignStageNotFoundError,
    DesignStagesNotAvailableError,
    DesignStageUnreviewedImpactError,
    DesignStageVersionConflictError,
    DesignUnitNotFoundError,
    DesignUnitProcedureNotFoundError,
)

logger = structlog.get_logger(__name__)

# 人が保存した段階の状態('draft'はAIの下書きだけが作る。UML図のSTATUS_AFTER_EDITと同じ考え方)
STATUS_AFTER_EDIT = "reviewing"

# 変更の影響を求める文書(model を持つ文書。モードの段階の入力)。簡易モードは4文書とも model を持つ
IMPACT_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "detailed": ("requirements", "external_design"),
    "simple": ("requirements", "external_design", "internal_design", "implementation_plan"),
}
# 段階3の写しに添える ER のテーブル定義の欄(ER は段階の model の外の図にあるため、承認したときの
# 定義を写しに残し、テーブルの内容(列の追加など)の差分を取れるようにする)
ER_SNAPSHOT_KEY = "er_snapshot"


class DesignStageService:
    """詳細設計モードの段階の取得・保存・承認を担当するサービス(全段階に共通の部分)。

    承認の条件は、全段階に共通の3つ(段階が開いている(入力がそろっている)、versionが一致する、
    承認できる状態で内容が空でない)と、段階ごとの検証(app/detailed_design/validation.py)で
    エラーが無いこと。AIの下書きの生成はdesign_stage_generation_service.pyが担い、
    生成中の段階はここで保存・承認を断る。

    簡易ドキュメントモードのプロジェクトは段階8(実装手順書)だけを持ち、入力は4文書
    (`stage_inputs`)。他の段階を求められたら断る(`_ensure_stage`)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._stages = DesignStageRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._diagrams = UmlDiagramRepository(session)

    async def list_stages(self, project: Project) -> list[DesignStageRead]:
        """モードの全段階の状態を返す(未着手の段階も含む。詳細設計モードは段階1〜8、
        簡易モードは段階8だけ)。"""
        rows, views, documents = await self._load(project)
        sources = await self._sources(project, rows, views, documents)
        findings = await self._generation_findings(project.id, rows.values())
        return [_to_read(views[stage], rows.get(stage), sources, findings) for stage in views]

    async def stage_view(
        self, project: Project, stage: int
    ) -> tuple[DesignStage | None, StageView, StageSources]:
        """段階1つ分の行(未着手ならNone)・状態・入力(文書の本文・承認済みの段階の内容・DFD の
        要約)を返す(下書きの生成が使う)。"""
        _ensure_stage(project, stage)
        rows, views, documents = await self._load(project)
        sources = await self._sources(project, rows, views, documents)
        return rows.get(stage), views[stage], sources

    async def overview(self, project: Project) -> tuple[dict[int, StageView], StageSources]:
        """モードの全段階の状態と、承認済みの段階の内容(`StageSources.stages`)を返す(詳細設計書・
        実装手順書の組み立てが使う)。"""
        rows, views, documents = await self._load(project)
        sources = await self._sources(project, rows, views, documents)
        return views, sources

    async def approved_stages(
        self, project: Project, stages: Sequence[int]
    ) -> dict[int, tuple[int, dict]]:
        """承認済みで古くない段階の、承認した版と内容(テスト仕様書の入力。承認されていない・古い
        段階は入れない)。"""
        rows, views, _ = await self._load(project)
        approved = _approved_versions(rows, views)
        return {
            stage: (version, rows[stage].approved_model or {})
            for stage in stages
            if (version := approved.get(stage)) is not None
        }

    async def current_fingerprint(self, project: Project, stage: int) -> Fingerprint:
        """段階が今入力にしているものの版(下書きの生成時に記録する。承認時と同じ規則)。"""
        rows, views, documents = await self._load(project)
        return current_inputs(
            stage,
            approved_stage_versions=_approved_versions(rows, views),
            doc_versions=_doc_versions(documents),
            inputs=stage_inputs(project.mode),
        )

    async def read(self, project_id: uuid.UUID, stage: int) -> DesignStageRead:
        """段階1つ分の今の状態を返す(モードを読むため、プロジェクトを読み直す)。"""
        project = await self._session.get(Project, project_id)
        if project is None:
            raise DesignStageNotFoundError(f"Project {project_id} does not exist")
        return await self._read_one(project, stage)

    async def save(
        self, project: Project, *, stage: int, expected_version: int | None, model: dict
    ) -> DesignStageRead:
        """段階の内容を人の編集として保存する。承認済みの段階を保存すると承認をやり直す
        (`reviewing`へ戻し、versionを増やす)。未着手の段階は、初めての保存で行を作る。
        段階7は、キーの無いタスク(画面で足したタスク)に単位のキーを振ってから保存する。"""
        _ensure_stage(project, stage)
        if stage == PLAN_STAGE:
            model = _with_unit_keys(model)
        rows, views, _ = await self._load(project)
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
        return await self._read_one(project, stage)

    async def mark_edited(self, project_id: uuid.UUID, stage: int) -> bool:
        """段階の内容のうち、段階の外に正本を持つもの(段階2の DFD・データ辞書)が直されたとき、
        承認済みの段階を人の編集と同じ扱いで差し戻す(`reviewing`へ戻し、versionを増やす)。
        差し戻したらTrue。

        段階の`model`の保存を経ずに内容が変わっても、承認をやり直させ、後ろの段階に「古い」を
        伝えるため(後ろの段階は、承認した版の番号で陳腐化を判定する)。承認済みでない段階は何も
        しない。行の無いプロジェクト(簡易ドキュメントモード)も何もしない。呼び出し元の保存と同じ
        トランザクションで使うため、commitしない。"""
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
        _ensure_stage(project, stage)
        rows, views, documents = await self._load(project)
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
        if blocking_marks(StageImpact.from_dict(row.impact), stage):
            raise DesignStageUnreviewedImpactError(
                f"Stage {stage} has unreviewed impact marks"
            )
        sources = await self._sources(project, rows, views, documents)
        if has_errors(validate_stage(stage, row.model, sources)):
            raise DesignStageInvalidError(f"Stage {stage} has validation errors")

        if stage == DATA_MODEL_STAGE:
            # 承認 = 人の確定なので、CRUD 図に残っている AI の下書きの印を外す
            row.model = confirm_drafts(row.model)
        # 承認でこの段階の印は消える。直前の承認の写しと今の内容の差分から、変更の影響を求めて
        # 届いた段階(この段階を含む)に印を記録し、写しを置き換える
        row.impact = None
        er = await self._er_tables(project.id) if project.mode == "detailed" else {}
        if row.approved_model and project.mode == "detailed":
            _record_stage_change(rows, documents, stage, row.approved_model, er)
        row.status = "approved"
        row.approved_version = row.version
        row.approved_model = (
            {**row.model, ER_SNAPSHOT_KEY: er} if stage == DATA_MODEL_STAGE else row.model
        )
        row.input_fingerprint = current_inputs(
            stage,
            approved_stage_versions=_approved_versions(rows, views),
            doc_versions=_doc_versions(documents),
            inputs=stage_inputs(project.mode),
        )
        await self._session.commit()
        await self._session.refresh(row)
        logger.info("design_stage_approved", project_id=str(project.id), stage=stage)
        return await self._read_one(project, stage)

    async def record_document_change(
        self,
        project: Project,
        doc_type: str,
        before_model: dict | None,
        after_model: dict | None,
    ) -> None:
        """文書の表示中の版が切り替わったとき(再生成・復元)、前に表示していた版の model と比べて、
        変更の影響を段階に印として記録する。model を持つ文書だけ(簡易モードは4文書、詳細設計モードは
        要件定義書・外部設計書)。どちらかの版に model が無ければ比べない。呼び出し元の版の切り替えと
        同じトランザクションで使うため、commitしない。"""
        if doc_type not in IMPACT_DOCUMENTS.get(project.mode, ()):
            return
        if not before_model or not after_model:
            return
        rows = {row.stage: row for row in await self._stages.list_for_project(project.id)}
        if all(row.approved_version is None for row in rows.values()):
            return
        inputs = stage_inputs(project.mode)
        documents = await self._current_documents(project.id, inputs)
        docs = _document_models(documents)
        stages = {stage: row.model for stage, row in rows.items()}
        if project.mode == "simple":
            # 簡易モードは段階8だけを持ち、単位の手順書が4文書を直接参照する
            before = build_simple_elements(
                SimpleModels.from_dicts(stages.get(8), {**docs, doc_type: before_model})
            )
            after = build_simple_elements(
                SimpleModels.from_dicts(stages.get(8), {**docs, doc_type: after_model})
            )
        else:
            er_tables = await self._er_tables(project.id)
            before = build_elements(
                DesignModels.from_dicts(stages, {**docs, doc_type: before_model}, er_tables)
            )
            after = build_elements(
                DesignModels.from_dicts(stages, {**docs, doc_type: after_model}, er_tables)
            )
        _record_impact(rows, doc_source(doc_type), before, after, stages=(DOCUMENT_STAGE,))

    async def review_marks(
        self, project: Project, *, stage: int, keys: list[str], reviewed: bool
    ) -> None:
        """段階の印を確認済み(または未確認)にする。知らないキーは無視する。"""
        _ensure_stage(project, stage)
        row = await self._stages.get(project_id=project.id, stage=stage)
        if row is None:
            raise DesignStageNotFoundError(f"Stage {stage} has not been started")
        impact = set_reviewed(StageImpact.from_dict(row.impact), keys, reviewed)
        row.impact = impact.to_dict()
        await self._session.commit()

    async def elements_since(
        self, project: Project, snapshots: Mapping[int, dict]
    ) -> dict[int, tuple[list[Element], list[Element]]]:
        """段階ごとに、写し(`snapshots`。レビューしたときの段階の内容)の要素と、今の内容の要素
        (その段階の置き場の要素だけ)。写しの側は、ほかの段階・文書を今のものとして組み立てる
        (承認の変更の影響と同じ求め方)。"""
        rows, _, documents = await self._load(project)
        stages = {s: row.model for s, row in rows.items()}
        docs = _document_models(documents)
        er = await self._er_tables(project.id) if project.mode == "detailed" else {}
        after = build_elements(DesignModels.from_dicts(stages, docs, er))
        result: dict[int, tuple[list[Element], list[Element]]] = {}
        for stage, snapshot in snapshots.items():
            before = build_elements(
                DesignModels.from_dicts(
                    {**stages, stage: snapshot}, docs, _snapshot_er(stage, snapshot, er)
                )
            )
            result[stage] = (
                [e for e in before if e.stage == stage],
                [e for e in after if e.stage == stage],
            )
        return result

    async def _er_tables(self, project_id: uuid.UUID) -> dict[str, dict]:
        """段階3の ER のテーブル名 → 定義(列と説明)。ER がまだ無ければ空。"""
        er = await self._diagrams.get_by_subject(
            project_id=project_id, notation="er", subject=ER_SUBJECT
        )
        return er_table_definitions(er.semantic_model) if er is not None else {}

    async def impact(self, project: Project) -> DesignImpactRead:
        """変更の影響(モードの全段階の、変わったものと印・再生成の候補)。記録した印に、まだ承認して
        いない編集の差分から求めた印(`pending`。承認すると記録される)を足して返す。"""
        rows, views, documents = await self._load(project)
        pending = await self._pending_impact(project, rows, documents)
        stages = [
            _stage_impact_read(stage, rows.get(stage), pending.get(stage, [])) for stage in views
        ]
        return DesignImpactRead(
            stages=stages, regeneration_targets=_regeneration_targets(stages)
        )

    async def stage_impact(self, project: Project, stage: int) -> StageImpactRead:
        """段階1つの、変わったものと印(確認済みの切り替えの応答)。"""
        _ensure_stage(project, stage)
        result = await self.impact(project)
        return next(item for item in result.stages if item.stage == stage)

    async def _pending_impact(
        self,
        project: Project,
        rows: dict[int, DesignStage],
        documents: dict[str, GeneratedDocument | None],
    ) -> dict[int, list[StageImpact]]:
        """承認の後に編集した段階の、写しと今の内容の差分から求めた印(届く段階ごと。保存しない)。"""
        edited = [
            stage
            for stage, row in rows.items()
            if row.approved_model and row.status != "approved"
        ]
        if project.mode != "detailed" or not edited:
            return {}
        stages = {s: row.model for s, row in rows.items()}
        docs = _document_models(documents)
        er = await self._er_tables(project.id)
        after = build_elements(DesignModels.from_dicts(stages, docs, er))
        pending: dict[int, list[StageImpact]] = {}
        for stage in edited:
            snapshot = rows[stage].approved_model or {}
            before = build_elements(
                DesignModels.from_dicts(
                    {**stages, stage: snapshot}, docs, _snapshot_er(stage, snapshot, er)
                )
            )
            found = _impact_by_stage(stage_source(stage), before, after, stages=(stage,))
            for target, impact in found.items():
                if _records_impact(rows.get(target)):
                    pending.setdefault(target, []).append(impact)
        return pending

    async def unit_context(self, project: Project, unit_id: str) -> UnitContextRead:
        """段階8の単位1つが参照する設計を展開して返す(画面の単位の詳細が使う)。中身は承認済みの
        段階1〜7(簡易モードは4文書)から毎回導き、保存しない。段階8が開いていなければ断る。"""
        _ensure_stage(project, PROCEDURE_DOC_STAGE)
        rows, views, documents = await self._load(project)
        _ensure_open(views[PROCEDURE_DOC_STAGE])
        sources = await self._sources(project, rows, views, documents)
        basis = _basis(sources)
        unit = find_unit(basis.plan, unit_id)
        if unit is None:
            raise DesignUnitNotFoundError(f"Unit {unit_id} is not in the plan")
        context = basis.context(unit)
        return UnitContextRead(
            unit_id=unit.unit_id,
            refs=[DesignRefRead.model_validate(ref, from_attributes=True) for ref in context.refs],
            crosscutting=context.crosscutting,
            environment=context.environment,
        )

    async def unit_ai_markdown(self, project: Project, unit_id: str) -> UnitAiMarkdownRead:
        """段階8の単位1つの AI 向けの版を、保存済みの手順書(下書き・レビュー中・古いものを含む)と
        承認済みの段階1〜7(簡易モードは4文書)から組み立てて返す(画面の「AI 向けにコピー」が
        使う)。段階8が承認済みでなければ、md の先頭で警告する。段階8が開いていなければ断り、
        作業単位に無い単位・手順書の無い単位は見つからないとする。"""
        _ensure_stage(project, PROCEDURE_DOC_STAGE)
        rows, views, documents = await self._load(project)
        view = views[PROCEDURE_DOC_STAGE]
        _ensure_open(view)
        sources = await self._sources(project, rows, views, documents)
        basis = _basis(sources)
        if find_unit(basis.plan, unit_id) is None:
            raise DesignUnitNotFoundError(f"Unit {unit_id} is not in the plan")
        row = rows.get(PROCEDURE_DOC_STAGE)
        model = row.model if row is not None else None
        try:
            source = procedure_output_source(
                project.title,
                view.state,
                basis,
                model,
                validate_stage(PROCEDURE_DOC_STAGE, model, sources),
                sources.documents.get("requirements", ""),
                sources.document_models.get("requirements"),
            )
        except ValidationError as exc:
            raise DesignUnitProcedureNotFoundError(
                f"Stage {PROCEDURE_DOC_STAGE} model is invalid"
            ) from exc
        key = unit_id.strip()
        if key not in source.procedures:
            raise DesignUnitProcedureNotFoundError(f"Unit {key} has no procedure document")
        findings = unit_findings(source, key)
        return UnitAiMarkdownRead(
            unit_id=key,
            markdown=to_ai_markdown(source, key),
            state=view.state,
            finding_total=len(findings),
            critical=count_by_level(findings)["critical"],
        )

    async def procedure_sequence(self, project: Project, function_id: str) -> SequenceRead:
        """段階5の処理1つのシーケンス図を、保存した手順(下書き・レビュー中を含む)から導いて返す
        (画面の段階5のタブが使う)。図は保存しない。依存先の指摘は承認済みの段階4を使う。
        段階5が開いていなければ断り、段階5で選んでいない処理は見つからないとする。"""
        _ensure_stage(project, PROCEDURE_STAGE)
        rows, views, _ = await self._load(project)
        _ensure_open(views[PROCEDURE_STAGE])
        row = rows.get(PROCEDURE_STAGE)
        try:
            model = ProcedureModel.model_validate(row.model if row is not None else {})
        except ValidationError as exc:
            raise DesignProcedureNotFoundError(f"Stage {PROCEDURE_STAGE} model is invalid") from exc
        procedure = next(
            (p for p in model.procedures if p.function_id.strip() == function_id.strip()), None
        )
        if procedure is None:
            raise DesignProcedureNotFoundError(
                f"Function {function_id} is not in stage {PROCEDURE_STAGE}"
            )
        structure = rows.get(STRUCTURE_STAGE)
        modules = (
            ModuleListModel.model_validate(structure.model)
            if structure is not None and views[STRUCTURE_STAGE].state == "approved"
            else None
        )
        diagram = to_sequence(procedure, module_dependencies(modules))
        return SequenceRead(
            function_id=procedure.function_id,
            svg=to_sequence_svg(diagram),
            issues=[
                SequenceIssueRead.model_validate(issue, from_attributes=True)
                for issue in diagram.issues
            ],
        )

    async def _load(
        self, project: Project
    ) -> tuple[
        dict[int, DesignStage], dict[int, StageView], dict[str, GeneratedDocument | None]
    ]:
        """モードの段階の行・状態と、入力の文書。モードに無い段階の行は読まない。"""
        inputs = stage_inputs(project.mode)
        rows = {
            row.stage: row
            for row in await self._stages.list_for_project(project.id)
            if row.stage in inputs
        }
        records = {stage: _to_record(row) for stage, row in rows.items()}
        documents = await self._current_documents(project.id, inputs)
        views = derive_states(records, _doc_versions(documents), inputs)
        return rows, views, documents

    async def _read_one(self, project: Project, stage: int) -> DesignStageRead:
        rows, views, documents = await self._load(project)
        sources = await self._sources(project, rows, views, documents)
        row = rows.get(stage)
        findings = await self._generation_findings(project.id, [row] if row is not None else [])
        return _to_read(views[stage], row, sources, findings)

    async def _generation_findings(
        self, project_id: uuid.UUID, rows: Iterable[DesignStage]
    ) -> dict[str, ReviewFinding]:
        """段階の直近の生成に渡した指摘を、ID の文字列で引けるようにまとめて1回で読む。"""
        ids = {
            uuid.UUID(finding_id)
            for row in rows
            for finding_id in (row.generation_finding_ids or [])
        }
        found = await ReviewRunRepository(self._session).list_findings(
            project_id=project_id, finding_ids=sorted(ids)
        )
        return {str(finding.id): finding for finding in found}

    async def _sources(
        self,
        project: Project,
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
        - 構成図: 段階4の構成図(全体1枚)の要約。

        - 文書の model: 要件定義書・外部設計書の表示中の版の model(要件の追跡が読む)。

        簡易モード(段階8だけ)は、文書だけを渡す(図は段階8の入力にならない)。
        """
        texts = {
            doc_type: document.content
            for doc_type, document in documents.items()
            if document is not None
        }
        approved = {
            stage: row.model
            for stage, row in rows.items()
            if views[stage].state == "approved" and row.model
        }
        models = {
            doc_type: document.model
            for doc_type, document in documents.items()
            if document is not None and document.model
        }
        if project.mode != "detailed":
            return StageSources(
                documents=texts, stages=approved, mode="simple", document_models=models
            )
        project_id = project.id
        diagrams = await self._diagrams.list_by_notation(project_id, "dfd")
        er = await self._diagrams.get_by_subject(
            project_id=project_id, notation="er", subject=ER_SUBJECT
        )
        component = await self._diagrams.get_by_subject(
            project_id=project_id, notation="component", subject=STRUCTURE_SUBJECT
        )
        return StageSources(
            documents=texts,
            stages=approved,
            document_models=models,
            dfd_diagrams={diagram.subject: _dfd_summary(diagram) for diagram in diagrams},
            er_diagram=_er_summary(er) if er is not None else None,
            component_diagram=_component_summary(component) if component is not None else None,
        )

    async def _current_documents(
        self, project_id: uuid.UUID, inputs: Mapping[int, StageInputs]
    ) -> dict[str, GeneratedDocument | None]:
        """段階の入力になる文書の、表示中(is_current)の版(まだ無ければNone)。"""
        doc_types = {d for stage_input in inputs.values() for d in stage_input.documents}
        return {
            doc_type: await self._documents.get_current(project_id=project_id, doc_type=doc_type)
            for doc_type in sorted(doc_types)
        }


def _doc_versions(documents: dict[str, GeneratedDocument | None]) -> dict[str, int | None]:
    return {
        doc_type: document.version if document is not None else None
        for doc_type, document in documents.items()
    }


def _document_models(documents: dict[str, GeneratedDocument | None]) -> dict[str, dict | None]:
    return {
        doc_type: document.model if document is not None else None
        for doc_type, document in documents.items()
    }


def _record_stage_change(
    rows: dict[int, DesignStage],
    documents: dict[str, GeneratedDocument | None],
    stage: int,
    snapshot: dict,
    er: dict[str, dict],
) -> None:
    """段階の承認時の写しと今の内容の差分から、変更の影響を印として記録する。"""
    stages = {s: row.model for s, row in rows.items()}
    docs = _document_models(documents)
    before = build_elements(
        DesignModels.from_dicts(
            {**stages, stage: snapshot}, docs, _snapshot_er(stage, snapshot, er)
        )
    )
    after = build_elements(DesignModels.from_dicts(stages, docs, er))
    _record_impact(rows, stage_source(stage), before, after, stages=(stage,))


def _snapshot_er(stage: int, snapshot: dict, er: dict[str, dict]) -> dict[str, dict]:
    """写しの側の ER。段階3の写しが承認したときの定義を持てばそれを、無ければ今の ER を使う
    (この移行より前の写し・他の段階の写しは、ER の差分を取らない)。"""
    if stage == DATA_MODEL_STAGE and isinstance(snapshot.get(ER_SNAPSHOT_KEY), dict):
        return snapshot[ER_SNAPSHOT_KEY]
    return er


def _impact_by_stage(
    source: str,
    before_elements: list[Element],
    after_elements: list[Element],
    *,
    stages: tuple[int, ...],
) -> dict[int, StageImpact]:
    """前と後の要素の差分(置き場`stages`の要素)から印を求め、届く段階ごとの記録にする。"""
    changes = diff_elements(before_elements, after_elements, stages=stages)
    if not changes:
        return {}
    marks = marks_for(before_elements, after_elements, changes, source)
    return {
        target: record(StageImpact(), source, changes, stage_marks)
        for target, stage_marks in group_by_stage(marks).items()
    }


def _records_impact(row: DesignStage | None) -> bool:
    """印を記録する段階か。一度でも承認した段階だけ(承認していない段階は、見直す前の内容が無い)。"""
    return row is not None and row.approved_version is not None


def _record_impact(
    rows: dict[int, DesignStage],
    source: str,
    before: list[Element],
    after: list[Element],
    *,
    stages: tuple[int, ...],
) -> None:
    """前と後の差分から印を求め、届いた段階の行に記録する(今の記録に足す)。"""
    for target, impact in _impact_by_stage(source, before, after, stages=stages).items():
        row = rows.get(target)
        if not _records_impact(row):
            continue
        assert row is not None
        changes = [c.change for c in impact.changes]
        merged = record(StageImpact.from_dict(row.impact), source, changes, impact.marks)
        row.impact = merged.to_dict()


def _stage_impact_read(
    stage: int, row: DesignStage | None, pending: list[StageImpact]
) -> StageImpactRead:
    recorded = StageImpact.from_dict(row.impact if row is not None else None)
    # 記録した印のあとに、まだ承認していない編集から届く印を並べる
    sources = [(recorded, False), *((impact, True) for impact in pending)]
    changes = [
        ElementChangeRead(source=c.source, pending=is_pending, **asdict(c.change))
        for impact, is_pending in sources
        for c in impact.changes
    ]
    marks = [
        ImpactMarkRead(
            key=m.key,
            label=m.label,
            stage=m.stage,
            path=list(m.path),
            reason=m.reason,
            sources=list(m.sources),
            reviewed=m.reviewed,
            pending=is_pending,
        )
        for impact, is_pending in sources
        for m in impact.marks
    ]
    return StageImpactRead(
        stage=stage,
        changes=changes,
        marks=marks,
        open=recorded.open_count,
        reviewed=recorded.reviewed_count,
    )


def _regeneration_targets(stages: list[StageImpactRead]) -> list[RegenerationTargetRead]:
    """再生成の候補: 部分再生成できる段階(5・6・8)の、未確認の印の要素(重複なし)。"""
    targets: dict[str, RegenerationTargetRead] = {}
    for item in stages:
        for mark in item.marks:
            if mark.stage in REGENERATION_STAGES and not mark.reviewed:
                target = RegenerationTargetRead(stage=mark.stage, key=mark.key, label=mark.label)
                targets.setdefault(mark.key, target)
    return list(targets.values())


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


def _component_summary(diagram: UmlDiagram) -> ComponentDiagramSummary:
    return ComponentDiagramSummary(
        status=diagram.status,
        generation_status=diagram.generation_status,
        layers=tuple(component_layers(diagram.semantic_model)),
        element_layers=component_element_layers(diagram.semantic_model),
    )


def _ensure_stage(project: Project, stage: int) -> None:
    """プロジェクトのモードに、その段階があるか(簡易モードは段階8だけ)。"""
    if stage not in stage_inputs(project.mode):
        raise DesignStagesNotAvailableError(
            f"Stage {stage} is not available for project {project.id} (mode={project.mode})"
        )


def _basis(sources: StageSources) -> ProcedureBasis:
    """段階8の土台(作業単位と参照の出どころ。モードで変わる)。"""
    return procedure_basis(
        sources.mode, sources.stages, sources.documents, sources.document_models
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


def _with_unit_keys(model: dict) -> dict:
    """段階7の内容に単位のキーを振る。形の正しくない内容はそのまま返す(検証が指摘する)。"""
    try:
        plan = PlanModel.model_validate(model)
    except ValidationError:
        return model
    return assign_unit_keys(plan).model_dump(mode="json")


def _relabeled(model: dict | None, basis: ProcedureBasis | None) -> dict | None:
    """段階8の手順書の表示の ID を、作業単位の今の並び順に付け直す(画面は ID で単位に付けるため)。
    作業単位が無い・形の正しくない内容はそのまま返す。"""
    if not model or basis is None:
        return model
    try:
        doc = ProcedureDocModel.model_validate(model)
    except ValidationError:
        return model
    return relabel_units(doc, basis.plan).model_dump(mode="json")


def _to_read(
    view: StageView,
    row: DesignStage | None,
    sources: StageSources,
    findings: Mapping[str, ReviewFinding] | None = None,
) -> DesignStageRead:
    # 段階8の作業単位(詳細設計モードは段階7、簡易モードは実装計画書の WBS)。画面の単位の一覧が使う
    basis = (
        _basis(sources) if view.stage == PROCEDURE_DOC_STAGE and view.is_open else None
    )
    plan = basis.plan.model_dump(mode="json") if basis is not None else None
    impact = StageImpact.from_dict(row.impact if row is not None else None)
    if row is not None:
        issues = validate_stage(view.stage, row.model, sources)
    elif view.is_open:
        # 内容の無い段階でも検証する段階がある(段階8は、手順書の無い単位にも指摘が出る)
        issues = validate_stage(view.stage, None, sources)
    else:
        issues = []
    return DesignStageRead(
        stage=view.stage,
        mode=sources.mode,
        state=view.state,
        is_open=view.is_open,
        missing_inputs=list(view.missing_inputs),
        version=row.version if row is not None else None,
        approved_version=row.approved_version if row is not None else None,
        model=_relabeled(row.model, basis) if row is not None else None,
        updated_at=row.updated_at if row is not None else None,
        generation_status=row.generation_status if row is not None else None,  # type: ignore[arg-type]
        generation_error=row.generation_error if row is not None else None,
        issues=[StageIssueRead.model_validate(issue, from_attributes=True) for issue in issues],
        dfd_accesses=_dfd_access_reads(sources) if view.stage == DATA_MODEL_STAGE else [],
        plan=plan,
        unit_req_ids=basis.unit_req_ids() if basis is not None else {},
        impact_open=impact.open_count,
        impact_reviewed=impact.reviewed_count,
        generation_findings=_generation_finding_reads(row, findings or {}),
    )


def _generation_finding_reads(
    row: DesignStage | None, findings: Mapping[str, ReviewFinding]
) -> list[GenerationFindingRead]:
    """直近の生成に渡した指摘を、渡した順に(消えた指摘は飛ばす)。"""
    if row is None:
        return []
    return [
        GenerationFindingRead(
            id=finding.id, level=finding.level, targets=finding.targets, problem=finding.problem
        )
        for finding_id in row.generation_finding_ids or []
        if (finding := findings.get(finding_id)) is not None
    ]


def _dfd_access_reads(sources: StageSources) -> list[DfdAccessRead]:
    """段階3の画面に渡す DFD の R/W。テーブル名は ER の名前に戻す(画面は ER の列の名前で引く)。"""
    names = {table_key(t): t for t in (sources.er_diagram.tables if sources.er_diagram else ())}
    return [
        DfdAccessRead(
            function_id=a.function_id, table=names.get(a.table, a.table), kind=a.kind
        )
        for a in selected_dfd_accesses(sources)
    ]
