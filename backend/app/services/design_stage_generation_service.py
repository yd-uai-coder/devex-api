"""詳細設計モードの段階のAIの下書きの生成(docs/external_design.md 2.7節「各段階の共通サイクル」)。

受け付け(`request_generation`、リクエスト内)と実行(`execute`、バックグラウンド)を分ける。
文書の生成と同じ形で、生成の経過は段階の行の
`generation_status`・`generation_error`に残し、画面は段階の一覧をポーリングして完了を待つ。

- 生成中は、同じ段階の生成・保存・承認を409で断る(AIの結果で人の編集を上書きしないため)。
- 下書きは`status='draft'`(初回)・`'regenerated'`(内容のある段階の作り直し)で保存し、
  `version`を1つ増やす(承認済みの段階を作り直すと承認はやり直しになる。UML図の再生成と同じ)。
  生成した時点の入力の版を`input_fingerprint`に記録する。前の承認の記録が残ると、作り直した
  直後でも「古い」と判定されるため。
- 15分を超えて生成中のまま止まった段階は、受け付け時と一覧の取得時に失敗へ戻す
  (app/services/generation_staleness.py)。
- 生成できる段階は`STAGE_GENERATORS`に登録したものだけ(段階1〜7)。
- 段階2は、段階の内容のほかに機能グループの DFD(`uml_diagrams`)とデータ項目(`data_items`)も
  書く。段階の保存と同じトランザクションで書き、失敗したらまとめて取り消す。そのため生成の関数には
  `StageGenerationContext`でセッションとプロジェクトを渡す。
- 段階3は、段階の内容(CRUD 図)のほかに ER(`uml_diagrams`、subject='')も書く。段階2と同じく
  1つのトランザクションで書く。
- 段階4は、段階の内容(モジュール一覧)のほかに構成図(`uml_diagrams`、notation=component、
  subject='')も書く。段階3と同じく1つのトランザクションで書く。
- 段階5は、処理ごとに下書きを作る(1処理 LLM 1回)。対象は受け付けで決め(指定が無ければ、選んだ
  処理のうちまだ手順の無いもの)、対象の処理の手順だけを置き換えて、他の処理の手直しは残す
  。
- 段階6は、段階5と同じく関数ごとに下書きを作る(1関数 LLM 1回)。対象は(モジュール, 関数)の鍵
  (`logic_key`)で受け渡す。
- 段階7は、横断事項と実装計画を順に下書きする(LLM 2回)。入力の詳細設計書は、出力と同じ組み立て
  (`DetailedDesignExportService.collect`・`to_markdown`)で 01〜06章の md にする。
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from langchain_core.messages import BaseMessage
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import get_gemini_llm
from app.core.database import AsyncSessionLocal
from app.detailed_design.data_flow import (
    MAX_DFD_GROUPS,
    DataFlowModel,
    dfd_subject,
    group_functions,
    merge_summaries,
)
from app.detailed_design.data_flow_drafting import (
    GroupDfdGenerationOutput,
    ProcessSummaryGenerationOutput,
    build_group_dfd_messages,
    build_summary_messages,
    to_dfd_output,
    to_summary_drafts,
)
from app.detailed_design.data_model import ER_SUBJECT, CrudModel, merge_crud
from app.detailed_design.data_model_drafting import (
    CrudGenerationOutput,
    DataModelErOutput,
    build_crud_messages,
    build_er_messages,
    data_store_names,
    to_crud_drafts,
    to_er_model,
)
from app.detailed_design.document import CHAPTERS, to_markdown
from app.detailed_design.drafting import (
    FunctionListGenerationOutput,
    build_function_list_messages,
    to_drafts,
)
from app.detailed_design.function_list import FunctionListModel, merge_draft
from app.detailed_design.logic import (
    LOGIC_STAGE,
    MAX_LOGIC_TARGETS,
    LogicModel,
    is_drafted,
    logic_key,
    merge_logic,
)
from app.detailed_design.logic import generation_targets as logic_generation_targets
from app.detailed_design.logic_drafting import (
    LogicGenerationOutput,
    build_logic_messages,
    calling_step_rows,
    to_logic_draft,
)
from app.detailed_design.plan import PLAN_STAGE, normalize_plan
from app.detailed_design.plan_drafting import (
    CrossCuttingGenerationOutput,
    PlanGenerationOutput,
    build_crosscutting_messages,
    build_plan_messages,
    to_crosscutting,
    to_plan_model,
)
from app.detailed_design.procedure import (
    MAX_PROCEDURE_TARGETS,
    PROCEDURE_STAGE,
    ProcedureModel,
    generation_targets,
    merge_procedure,
)
from app.detailed_design.procedure_drafting import (
    ProcedureGenerationOutput,
    build_procedure_messages,
    to_procedure_draft,
)
from app.detailed_design.stages import Fingerprint
from app.detailed_design.structure import STRUCTURE_SUBJECT, ModuleListModel, merge_modules
from app.detailed_design.structure_drafting import (
    ModuleListGenerationOutput,
    build_component_messages,
    build_module_messages,
    to_module_drafts,
)
from app.detailed_design.validation import StageSources, selected_dfd_accesses
from app.models.project import Project
from app.repositories.design_stage import DesignStageRepository
from app.repositories.project import ProjectRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.schemas.design_stage import DesignStageRead
from app.services.data_item_service import DataItemService
from app.services.design_stage_service import DesignStageService
from app.services.detailed_design_export_service import DetailedDesignExportService
from app.services.errors import (
    DesignStageGenerationInProgressError,
    DesignStageGenerationNotSupportedError,
    DesignStageInvalidError,
    DesignStageLockedError,
)
from app.services.generation_staleness import is_stale
from app.services.llm_retry import invoke_with_retry
from app.uml.domain import NOTATION_TO_VIEW, NotationType
from app.uml.generation.failures import ReasonCode, classify_failure, unwrap_structured_result
from app.uml.generation.mapper import required_data_items, to_component, to_dfd
from app.uml.generation.prompts import ExistingDataItem
from app.uml.generation.schemas import ComponentGenerationOutput

logger = structlog.get_logger(__name__)

# 段階の下書きが止まった理由のユーザー向けの文言(UML図の文言は「設計図」「ER図」を前提にしている
# ため、段階用に言い換える。理由の分類自体は classify_failure を共有する)
_MESSAGES: dict[ReasonCode, str] = {
    "QUOTA_EXCEEDED": (
        "AIの利用上限に達したため、下書きを作れませんでした。"
        "時間をおいて、もう一度生成してください。"
    ),
    "TOKEN_LIMIT": (
        "AIの入力または出力のトークン数が上限を超えたため、下書きを作れませんでした。"
    ),
    "INVALID_OUTPUT": "AIの出力を下書きとして解釈できませんでした。もう一度生成してください。",
    "GENERATION_FAILED": "下書きの生成に失敗しました。時間をおいて、もう一度生成してください。",
    "STALE_GENERATION": (
        "下書きの生成が時間内に終わらなかったため、中断しました。もう一度生成してください。"
    ),
}

@dataclass(frozen=True)
class StageGenerationContext:
    """下書きの生成の関数に渡す入力。

    - `sources`: 入力の文書の本文と、入力の段階の内容(承認済み)。
    - `previous`: その段階の今の内容(作り直しのとき。初回は None)。
    - `fingerprint`: 生成した時点の入力の版(段階2は DFD の`source_doc_versions`にも記録する)。
    - `session`・`project_id`: 段階のほかに DB へ書く生成(段階2の DFD・データ項目)が使う。
      commitは呼び出し元(execute)が段階の保存と一緒に1回だけ行う。
    - `targets`: 段階5で下書きを作る処理の処理ID、段階6で下書きを作る関数の鍵(`logic_key`)。
      受け付けで決めた順。他の段階は空。
    """

    llm: Any
    sources: StageSources
    previous: Mapping[str, Any] | None
    fingerprint: Fingerprint
    session: AsyncSession
    project_id: uuid.UUID
    targets: tuple[str, ...] = ()


StageGenerator = Callable[[StageGenerationContext], Awaitable[dict]]


async def _invoke_structured[T: BaseModel](
    llm, schema: type[T], messages: list[BaseMessage]
) -> T:
    """構造化出力を呼ぶ(解釈の失敗・クォータ超過は invoke_with_retry の規則で再試行する)。"""
    structured_llm = llm.with_structured_output(schema, include_raw=True)

    async def _call() -> T:
        result = await structured_llm.ainvoke(messages)
        return unwrap_structured_result(result, schema)

    return await invoke_with_retry(_call, messages=messages)


async def generate_function_list(context: StageGenerationContext) -> dict:
    """段階1: 外部設計書から処理を下書きし、前の版と突き合わせて処理IDと機能グループを決める。"""
    messages = build_function_list_messages(context.sources.documents.get("external_design", ""))
    output = await _invoke_structured(context.llm, FunctionListGenerationOutput, messages)
    previous = context.previous
    previous_model = FunctionListModel.model_validate(previous) if previous else None
    return merge_draft(to_drafts(output), previous_model).model_dump(mode="json")


async def generate_data_flow(context: StageGenerationContext) -> dict:
    """段階2: 全処理の処理概要表を下書きし、人が選んだ機能グループごとに DFD を下書きする。

    DFD を描くグループ(`dfd_groups`)は人の選択なので、前の版から引き継ぐ。グループの DFD は
    `uml_diagrams`の同じ行(subject=機能グループ名)を上書きし、承認はやり直しになる。"""
    function_list = FunctionListModel.model_validate(context.sources.stages.get(1) or {})
    previous = DataFlowModel.model_validate(context.previous) if context.previous else None
    requirements = context.sources.documents.get("requirements", "")

    summary = await _invoke_structured(
        context.llm,
        ProcessSummaryGenerationOutput,
        build_summary_messages(function_list.functions, requirements),
    )
    model = merge_summaries(to_summary_drafts(summary), function_list, previous)

    data_items = DataItemService(context.session)
    for group in model.dfd_groups:
        functions = group_functions(function_list, group)
        existing = [
            ExistingDataItem(name=item.name, field_names=[f["name"] for f in item.fields])
            for item in await data_items.list_for_project(context.project_id)
        ]
        output = await _invoke_structured(
            context.llm,
            GroupDfdGenerationOutput,
            build_group_dfd_messages(group, functions, model.summaries, requirements, existing),
        )
        converted = to_dfd_output(output, functions)
        ids_by_name = await data_items.resolve_by_name(
            context.project_id, required_data_items(converted)
        )
        await _save_diagram(context, "dfd", dfd_subject(group), to_dfd(converted, ids_by_name))
    return model.model_dump(mode="json")


async def generate_data_model(context: StageGenerationContext) -> dict:
    """段階3: DFD のデータストアとデータ辞書から ER(テーブル定義を含む)を下書きし、そのテーブルで
    CRUD 図を下書きする。DFD の線から決まる R/W は`merge_crud`が足す。

    ER は`uml_diagrams`の同じ行(subject='')を上書きし、承認はやり直しになる。CRUD 図は前の版を
    使わずに置き換える(作り直しは、段階2が変わって DFD の R/W が変わったときに行うため)。"""
    function_list = FunctionListModel.model_validate(context.sources.stages.get(1) or {})
    data_flow = DataFlowModel.model_validate(context.sources.stages.get(2) or {})
    accesses = selected_dfd_accesses(context.sources)
    existing = [
        ExistingDataItem(name=item.name, field_names=[f["name"] for f in item.fields])
        for item in await DataItemService(context.session).list_for_project(context.project_id)
    ]

    er_output = await _invoke_structured(
        context.llm,
        DataModelErOutput,
        build_er_messages(data_store_names(accesses), existing, data_flow.summaries),
    )
    er = to_er_model(er_output)
    await _save_diagram(context, "er", ER_SUBJECT, er)

    tables = [element.name for element in er.elements]
    crud_output = await _invoke_structured(
        context.llm,
        CrudGenerationOutput,
        build_crud_messages(function_list.functions, data_flow.summaries, tables, accesses),
    )
    model = merge_crud(to_crud_drafts(crud_output), accesses, function_list, tables)
    return model.model_dump(mode="json")


async def generate_structure(context: StageGenerationContext) -> dict:
    """段階4: 技術スタック・機能一覧・データモデルから構成図(パッケージ単位・層のレーン)を下書きし、
    その構成図のパッケージをファイル単位に分けたモジュール一覧を下書きする。

    構成図は`uml_diagrams`の同じ行(notation=component、subject='')を上書きし、承認はやり直しに
    なる。モジュール一覧は前の版を使わずに置き換える(段階3の CRUD 図と同じ)。"""
    function_list = FunctionListModel.model_validate(context.sources.stages.get(1) or {})
    data_flow = DataFlowModel.model_validate(context.sources.stages.get(2) or {})
    crud = CrudModel.model_validate(context.sources.stages.get(3) or {})
    requirements = context.sources.documents.get("requirements", "")
    er = context.sources.er_diagram
    tables = list(er.tables) if er is not None else []

    component_output = await _invoke_structured(
        context.llm,
        ComponentGenerationOutput,
        build_component_messages(
            requirements, function_list.functions, data_flow.summaries, tables
        ),
    )
    component = to_component(component_output)
    await _save_diagram(context, "component", STRUCTURE_SUBJECT, component)

    module_output = await _invoke_structured(
        context.llm,
        ModuleListGenerationOutput,
        build_module_messages(
            component,
            requirements,
            function_list.functions,
            data_flow.summaries,
            crud.cells,
            tables,
        ),
    )
    model = merge_modules(to_module_drafts(module_output), function_list)
    return model.model_dump(mode="json")


async def generate_procedures(context: StageGenerationContext) -> dict:
    """段階5: 対象の処理ごとに、手順を下書きする(1処理 LLM 1回)。

    対象の処理の手順だけを置き換え、他の処理(人が手直しした手順)はそのまま残す。呼び出し先は
    `merge_procedure`がモジュール一覧のパスにそろえる。"""
    function_list = FunctionListModel.model_validate(context.sources.stages.get(1) or {})
    data_flow = DataFlowModel.model_validate(context.sources.stages.get(2) or {})
    crud = CrudModel.model_validate(context.sources.stages.get(3) or {})
    modules = ModuleListModel.model_validate(context.sources.stages.get(4) or {}).modules
    er = context.sources.er_diagram
    tables = list(er.tables) if er is not None else []
    accesses = selected_dfd_accesses(context.sources)
    functions = {row.id: row for row in function_list.functions}
    summaries = {row.function_id: row for row in data_flow.summaries}
    paths = [row.path for row in modules]

    model = ProcedureModel.model_validate(context.previous or {})
    for function_id in context.targets:
        function = functions.get(function_id)
        if function is None:
            # 受け付けの後に段階1が変わった処理(検証の UNKNOWN_FUNCTION で人に知らせる)
            continue
        output = await _invoke_structured(
            context.llm,
            ProcedureGenerationOutput,
            build_procedure_messages(
                function, summaries.get(function_id), accesses, crud.cells, tables, modules
            ),
        )
        model = merge_procedure(model, function_id, to_procedure_draft(output), paths)
    return model.model_dump(mode="json")


async def generate_logics(context: StageGenerationContext) -> dict:
    """段階6: 対象の関数ごとに、詳細を下書きする(1関数 LLM 1回)。

    対象の関数の詳細だけを置き換え、他の関数(人が手直しした詳細)はそのまま残す。呼ばれる手順は、
    承認済みの段階5の手順から(呼び出し先, 関数)の一致で集める。"""
    function_list = FunctionListModel.model_validate(context.sources.stages.get(1) or {})
    modules = ModuleListModel.model_validate(context.sources.stages.get(4) or {}).modules
    procedures = ProcedureModel.model_validate(context.sources.stages.get(5) or {})
    er = context.sources.er_diagram
    tables = list(er.tables) if er is not None else []
    functions = {row.id: row for row in function_list.functions}
    modules_by_path = {row.path.strip(): row for row in modules}

    model = LogicModel.model_validate(context.previous or {})
    rows = {logic_key(row.module, row.function): row for row in model.logics}
    for key in context.targets:
        row = rows.get(key)
        if row is None:
            continue
        output = await _invoke_structured(
            context.llm,
            LogicGenerationOutput,
            build_logic_messages(
                row.module,
                row.function,
                modules_by_path.get(row.module.strip()),
                calling_step_rows(procedures, row.module, row.function),
                functions,
                tables,
            ),
        )
        model = merge_logic(model, key, to_logic_draft(output))
    return model.model_dump(mode="json")


async def generate_plan(context: StageGenerationContext) -> dict:
    """段階7: 横断事項を下書きし、それを入力に実装計画を下書きする(LLM 2回)。

    詳細設計書は 01〜06章だけを md にして渡す(07 横断事項は、この段階自身が作るため)。図は
    描かない(md の画像は入力に要らず、図を`exported`にもしない)。作り直しは全体を置き換える
    (段階4と同じ)。"""
    project = await context.session.get(Project, context.project_id)
    if project is None:
        raise RuntimeError("project not found")
    collected = await DetailedDesignExportService(context.session).collect(project, render=False)
    chapters = [chapter for chapter in CHAPTERS if chapter.stage < PLAN_STAGE]
    design = to_markdown(collected.source, chapters)
    requirements = context.sources.documents.get("requirements", "")
    external_design = context.sources.documents.get("external_design", "")
    function_list = FunctionListModel.model_validate(context.sources.stages.get(1) or {})
    modules = ModuleListModel.model_validate(context.sources.stages.get(4) or {}).modules

    crosscutting_output = await _invoke_structured(
        context.llm,
        CrossCuttingGenerationOutput,
        build_crosscutting_messages(requirements, external_design, design),
    )
    crosscutting = to_crosscutting(crosscutting_output)
    plan_output = await _invoke_structured(
        context.llm,
        PlanGenerationOutput,
        build_plan_messages(requirements, design, crosscutting, function_list.functions),
    )
    model = normalize_plan(to_plan_model(crosscutting, plan_output), [row.path for row in modules])
    return model.model_dump(mode="json")


async def _save_diagram(
    context: StageGenerationContext, notation: NotationType, subject: str, model: BaseModel
) -> None:
    """段階の図(段階2の DFD・段階3の ER・段階4の構成図)を、同じ notation・subject の行に
    上書きする(無ければ作る)。commitしない。配置は消して、画面で最初に開いたときに自動レイアウト
    させる(UML図の再生成と同じ)。"""
    diagrams = UmlDiagramRepository(context.session)
    diagram = await diagrams.get_by_subject(
        project_id=context.project_id, notation=notation, subject=subject
    )
    if diagram is None:
        diagram = await diagrams.create(
            project_id=context.project_id,
            view=NOTATION_TO_VIEW[notation],
            notation=notation,
            semantic_model={},
            subject=subject,
        )
    diagram.semantic_model = model.model_dump(mode="json")
    diagram.layout_model = None
    diagram.status = "draft"
    diagram.version += 1
    diagram.source_doc_versions = dict(context.fingerprint)
    diagram.generation_status = "completed"
    diagram.generation_error = None


# 段階番号 → 下書きの生成。登録の無い段階は生成できない(各段階の Phase で足す)。
STAGE_GENERATORS: dict[int, StageGenerator] = {
    1: generate_function_list,
    2: generate_data_flow,
    3: generate_data_model,
    4: generate_structure,
    5: generate_procedures,
    6: generate_logics,
    7: generate_plan,
}


def _has_draft(
    stage: int, model: Mapping[str, Any] | None, targets: tuple[str, ...] = ()
) -> bool:
    """作り直し(`regenerated`)か初回(`draft`)かの判定に使う、「下書きの内容がある」か。
    段階2は、人がグループの選択だけを保存してから初めて生成するので、処理概要表の行で判定する。
    段階5は、対象の処理にもともと手順があったか(他の処理の手順は置き換えないので数えない)。
    段階6も同じく、対象の関数にもともと詳細があったか。"""
    if not model:
        return False
    if stage == 2:
        return bool(model.get("summaries"))
    if stage == PROCEDURE_STAGE:
        procedures = ProcedureModel.model_validate(model).procedures
        return any(p.steps for p in procedures if p.function_id in targets)
    if stage == LOGIC_STAGE:
        logics = LogicModel.model_validate(model).logics
        return any(is_drafted(r) for r in logics if logic_key(r.module, r.function) in targets)
    return True


LogicTargets = list[tuple[str, str]]


def _targets(
    stage: int,
    model: Mapping[str, Any] | None,
    function_ids: list[str] | None,
    logics: LogicTargets | None = None,
) -> tuple[str, ...]:
    """段階5で下書きを作る処理・段階6で下書きを作る関数の鍵(他の段階は空)。"""
    if stage == PROCEDURE_STAGE:
        return tuple(generation_targets(ProcedureModel.model_validate(model or {}), function_ids))
    if stage == LOGIC_STAGE:
        return tuple(logic_generation_targets(LogicModel.model_validate(model or {}), logics))
    return ()


def _check_request(
    stage: int,
    model: Mapping[str, Any] | None,
    function_ids: list[str] | None,
    logics: LogicTargets | None = None,
) -> None:
    """段階ごとの、生成を受け付ける前の確認。段階2は DFD を描くグループの数(上限を超えたまま
    生成すると、15分の回収のしきい値を超えるおそれがあるため)。段階5は下書きを作る処理(空・
    選ばれていない処理・上限を超える数を断る)。段階6は下書きを作る関数(段階5と同じ規則)。
    処理の指定は段階5だけ、関数の指定は段階6だけが受け付ける。"""
    if function_ids is not None and stage != PROCEDURE_STAGE:
        raise DesignStageInvalidError("処理を指定して生成できるのは段階5だけです。")
    if logics is not None and stage != LOGIC_STAGE:
        raise DesignStageInvalidError("関数を指定して生成できるのは段階6だけです。")
    if stage == LOGIC_STAGE:
        targets = _targets(stage, model, None, logics)
        selected = {
            logic_key(r.module, r.function) for r in LogicModel.model_validate(model or {}).logics
        }
        if not targets:
            raise DesignStageInvalidError(
                "下書きを作る関数がありません(詳細を書く関数を選んで保存してください)。"
            )
        unknown = [t.replace("::", " の ") for t in targets if t not in selected]
        if unknown:
            raise DesignStageInvalidError(f"選ばれていない関数です: {', '.join(unknown)}")
        if len(targets) > MAX_LOGIC_TARGETS:
            raise DesignStageInvalidError(
                f"1回に下書きを作れる関数は {MAX_LOGIC_TARGETS} つまでです"
                f"(今は {len(targets)} つ)。"
            )
    if stage == PROCEDURE_STAGE:
        targets = _targets(stage, model, function_ids)
        selected = {p.function_id for p in ProcedureModel.model_validate(model or {}).procedures}
        if not targets:
            raise DesignStageInvalidError(
                "下書きを作る処理がありません(手順を書く処理を選んで保存してください)。"
            )
        unknown = [t for t in targets if t not in selected]
        if unknown:
            raise DesignStageInvalidError(f"選ばれていない処理です: {', '.join(unknown)}")
        if len(targets) > MAX_PROCEDURE_TARGETS:
            raise DesignStageInvalidError(
                f"1回に下書きを作れる処理は {MAX_PROCEDURE_TARGETS} つまでです"
                f"(今は {len(targets)} つ)。"
            )
    if stage == 2 and model:
        groups = set(DataFlowModel.model_validate(model).dfd_groups)
        if len(groups) > MAX_DFD_GROUPS:
            raise DesignStageInvalidError(
                f"DFD を描く機能グループは {MAX_DFD_GROUPS} つまでです(今は {len(groups)} つ)。"
            )


async def run_design_stage_generation(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    stage: int,
    function_ids: list[str] | None = None,
    logics: LogicTargets | None = None,
    *,
    llm=None,
) -> None:
    """`BackgroundTasks`から呼び出すエントリポイント。リクエストのセッションはbackground task
    実行前にクローズされるため、セッションを自前で開始・終了する(run_uml_generationと同じ形)。"""
    async with AsyncSessionLocal() as session:
        await DesignStageGenerationService(session).execute(
            project_id=project_id,
            user_id=user_id,
            stage=stage,
            function_ids=function_ids,
            logics=logics,
            llm=llm,
        )


class DesignStageGenerationService:
    """段階のAIの下書きの生成を担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._stages = DesignStageService(session)
        self._rows = DesignStageRepository(session)
        self._projects = ProjectRepository(session)

    async def request_generation(
        self,
        project: Project,
        *,
        stage: int,
        function_ids: list[str] | None = None,
        logics: LogicTargets | None = None,
    ) -> DesignStageRead:
        """生成を受け付け、段階を「生成中」にする。生成自体は呼び出し元がバックグラウンドで
        `execute`する。未着手の段階は、ここで行を作る(内容は空、`draft`)。

        断る条件(この順): 詳細設計モードでない / 生成に対応していない段階 / 段階が開いていない /
        その段階を生成中 / 段階ごとの確認(段階2の DFD を描くグループの数、段階5の対象の処理、
        段階6の対象の関数)。"""
        await self.recover_stale(project.id)
        row, view, _ = await self._stages.stage_view(project, stage)
        if stage not in STAGE_GENERATORS:
            raise DesignStageGenerationNotSupportedError(
                f"Stage {stage} does not support AI drafts yet"
            )
        if not view.is_open:
            raise DesignStageLockedError(
                f"Stage {stage} is locked: missing {', '.join(view.missing_inputs)}"
            )
        if row is not None and row.generation_status == "generating":
            raise DesignStageGenerationInProgressError(
                "この段階の下書きを生成中です。完了してから再度お試しください。"
            )
        _check_request(stage, row.model if row is not None else None, function_ids, logics)
        if row is None:
            row = await self._rows.create(project_id=project.id, stage=stage, model=None)
        row.generation_status = "generating"
        row.generation_error = None
        row.generation_started_at = datetime.now(UTC)
        await self._session.commit()
        logger.info("design_stage_generation_requested", project_id=str(project.id), stage=stage)
        return await self._stages.read(project.id, stage)

    async def execute(
        self,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        stage: int,
        function_ids: list[str] | None = None,
        logics: LogicTargets | None = None,
        llm=None,
    ) -> None:
        """下書きを生成して段階に保存する。失敗したら理由を残して`failed`にする。
        プロジェクトや「生成中」の段階が見つからなければ何もしない(回収された後など)。"""
        project = await self._projects.get_by_id(project_id, user_id=user_id)
        if project is None:
            return
        row, _, sources = await self._stages.stage_view(project, stage)
        if row is None or row.generation_status != "generating":
            return
        generator = STAGE_GENERATORS[stage]
        # 受け付けの後は生成中で保存できないので、受け付けと同じ対象になる
        targets = _targets(stage, row.model, function_ids, logics)
        regenerating = _has_draft(stage, row.model, targets)
        fingerprint = await self._stages.current_fingerprint(project, stage)
        context = StageGenerationContext(
            llm=llm or get_gemini_llm(),
            sources=sources,
            previous=row.model,
            fingerprint=fingerprint,
            session=self._session,
            project_id=project_id,
            targets=targets,
        )
        try:
            model = await generator(context)
        except Exception as exc:
            # 途中の変更(段階2の DFD・データ項目も)を残さず、読み直した行に失敗だけを記録する
            # (rollbackで行は期限切れになる)
            await self._session.rollback()
            row = await self._rows.get(project_id=project_id, stage=stage)
            if row is None:
                return
            failure = classify_failure(exc)
            logger.warning(
                "design_stage_generation_failed",
                project_id=str(project_id),
                stage=stage,
                reason_code=failure.reason_code,
                error=str(exc),
            )
            row.generation_status = "failed"
            row.generation_error = _MESSAGES[failure.reason_code]
            await self._session.commit()
            return

        row.model = model
        row.status = "regenerated" if regenerating else "draft"
        row.input_fingerprint = fingerprint
        row.version += 1
        row.generation_status = "completed"
        row.generation_error = None
        await self._session.commit()
        logger.info("design_stage_generation_completed", project_id=str(project_id), stage=stage)

    async def recover_stale(self, project_id: uuid.UUID, *, now: datetime | None = None) -> int:
        """しきい値(15分)を超えて生成中のまま止まった段階を`failed`にする。回収した数を返す。
        止まった段階が残ると、その段階の生成・保存・承認が409で塞がり続けるため。"""
        now = now or datetime.now(UTC)
        stale = [
            row
            for row in await self._rows.list_for_project(project_id)
            if row.generation_status == "generating"
            and (row.generation_started_at is None or is_stale(row.generation_started_at, now))
        ]
        for row in stale:
            row.generation_status = "failed"
            row.generation_error = _MESSAGES["STALE_GENERATION"]
        if stale:
            await self._session.commit()
            logger.warning(
                "design_stage_generation_recovered", project_id=str(project_id), count=len(stale)
            )
        return len(stale)
