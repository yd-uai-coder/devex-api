"""段階4の下書きの入出力(プロンプト・出力スキーマ・モジュール一覧への変換)のテスト。

SUT: build_component_messages / build_module_messages / to_module_drafts
     (app/detailed_design/structure_drafting.py)、E2E 用の固定の出力(app/ai/llm/fake.py)、
     段階1〜4のプロンプトの表記の規則 NAMING_RULES(app/detailed_design/prompt_rules.py。
     drafting.py・data_flow_drafting.py・data_model_drafting.py・structure_drafting.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、LLM を呼ばないため(メッセージを組み立て、
構造化出力を受け取って変換するだけ。LLM の呼び出しはサービス層の責務)。
"""

from app.ai.llm.fake import _UML_OUTPUTS
from app.detailed_design import (
    CrudCell,
    FunctionListModel,
    ProcessSummaryRow,
    StageSources,
    component_layers,
    data_flow_drafting,
    data_model_drafting,
    drafting,
    merge_modules,
    validate_stage,
)
from app.detailed_design.prompt_rules import NAMING_RULES
from app.detailed_design.structure_drafting import (
    COMPONENT_SYSTEM_PROMPT,
    MAX_COMPONENTS,
    MODULE_SYSTEM_PROMPT,
    GeneratedModuleRow,
    ModuleListGenerationOutput,
    build_component_messages,
    build_module_messages,
    to_module_drafts,
)
from app.detailed_design.validation import ComponentDiagramSummary
from app.uml.generation.mapper import to_component
from app.uml.generation.schemas import ComponentGenerationOutput

_FUNCTIONS = FunctionListModel.model_validate(
    {
        "groups": ["予約"],
        "functions": [
            {"id": "F-01", "name": "予約を登録する", "group": "予約", "trigger": "POST /r"},
            {"id": "F-02", "name": "予約を一覧する", "group": "予約", "trigger": "GET /r"},
        ],
        "next_number": 3,
    }
)
_SUMMARIES = [ProcessSummaryRow(function_id="F-01", input="予約", process="保存", output="予約")]


def _component():
    return to_component(_UML_OUTPUTS[ComponentGenerationOutput])  # type: ignore[arg-type]


# --- 統合スモーク: E2E の固定の出力で、構成図 → モジュール一覧 → 検証まで通る ---


def test_smoke_fake_outputs_pass_validation():
    component = _component()
    output = _UML_OUTPUTS[ModuleListGenerationOutput]
    model = merge_modules(to_module_drafts(output), _FUNCTIONS)  # type: ignore[arg-type]
    sources = StageSources(
        stages={1: _FUNCTIONS.model_dump()},
        component_diagram=ComponentDiagramSummary(
            status="approved",
            generation_status="completed",
            layers=tuple(component_layers(component.model_dump())),
        ),
    )
    assert validate_stage(4, model.model_dump(), sources) == []


# --- build_component_messages ---


def test_component_messages_pass_requirements_functions_and_tables():
    messages = build_component_messages(
        "技術: FastAPI", _FUNCTIONS.functions, _SUMMARIES, ["users"]
    )
    assert messages[0].content == COMPONENT_SYSTEM_PROMPT
    assert f"{MAX_COMPONENTS}個以内" in COMPONENT_SYSTEM_PROMPT
    human = str(messages[1].content)
    assert "技術: FastAPI" in human
    assert "F-01 予約を登録する" in human and "トリガー: POST /r" in human
    assert "F-01: 入力=予約" in human
    assert "- users" in human


def test_component_messages_mark_missing_inputs():
    human = str(build_component_messages("", _FUNCTIONS.functions, [], [])[1].content)
    assert human.count("(ありません)") == 3


# --- build_module_messages ---


def test_module_messages_describe_component_and_crud():
    cells = [CrudCell(function_id="F-01", table="reservations", ops="C")]
    messages = build_module_messages(
        _component(), "要件", _FUNCTIONS.functions, _SUMMARIES, cells, ["reservations"]
    )
    assert messages[0].content == MODULE_SYSTEM_PROMPT
    human = str(messages[1].content)
    assert "- api(層: api)" in human
    assert "- api → service" in human
    assert "- F-01 × reservations: C" in human
    assert "## テーブル\n- reservations" in human


# --- to_module_drafts ---


def test_to_module_drafts_keeps_fields():
    output = ModuleListGenerationOutput(
        modules=[
            GeneratedModuleRow(
                path="app/a.py",
                layer="api",
                responsibility="A",
                depends_on=["app/b.py"],
                functions=["F-02"],
                all_functions=True,
            )
        ]
    )
    [draft] = to_module_drafts(output)
    assert draft.path == "app/a.py"
    assert draft.depends_on == ("app/b.py",)
    assert draft.functions == ("F-02",)
    assert draft.all_functions is True


# --- 表記の規則(画面確認後の修正) ---


def test_all_stage_prompts_end_with_naming_rules():
    """段階1〜4の下書きの system プロンプトは、すべて共通の表記の規則で終わる。"""
    prompts = [
        drafting.SYSTEM_PROMPT,
        data_flow_drafting.SUMMARY_SYSTEM_PROMPT,
        data_flow_drafting.DFD_SYSTEM_PROMPT,
        data_model_drafting.ER_SYSTEM_PROMPT,
        data_model_drafting.CRUD_SYSTEM_PROMPT,
        COMPONENT_SYSTEM_PROMPT,
        MODULE_SYSTEM_PROMPT,
    ]
    assert all(prompt.endswith(NAMING_RULES) for prompt in prompts)
    assert "日本語" in NAMING_RULES and "英語" in NAMING_RULES
    # 構成図の箱は「日本語の名称(ディレクトリ)」で併記させる
    assert "サービス(services)" in COMPONENT_SYSTEM_PROMPT
