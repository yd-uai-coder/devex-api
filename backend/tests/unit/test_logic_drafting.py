"""段階6の下書きの入出力(呼ばれる手順の集め方・プロンプト・出力スキーマ・詳細への変換)のテスト。

SUT: calling_step_rows / build_logic_messages / to_logic_draft
     (app/detailed_design/logic_drafting.py)、
     E2E 用の固定の出力(app/ai/llm/fake.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、LLM を呼ばないため(メッセージを組み立て、
構造化出力を受け取って変換するだけ。LLM の呼び出しはサービス層の責務)。
"""

from tests.fixtures.detailed_design import (
    function_list_model,
    module_list_model,
    procedure_model,
)

from app.ai.llm.fake import _UML_OUTPUTS
from app.detailed_design import (
    FunctionListModel,
    LogicModel,
    LogicRow,
    ModuleListModel,
    ProcedureModel,
    StageSources,
    logic_key,
    merge_logic,
    validate_stage,
)
from app.detailed_design.logic_drafting import (
    LOGIC_SYSTEM_PROMPT,
    GeneratedPseudoStep,
    LogicGenerationOutput,
    build_logic_messages,
    calling_step_rows,
    to_logic_draft,
)
from app.detailed_design.prompt_rules import NAMING_RULES

ROUTE = "app/api/routes/reservations.py"
_PROCEDURES = ProcedureModel.model_validate(procedure_model())
_FUNCTIONS = {f.id: f for f in FunctionListModel.model_validate(function_list_model()).functions}
_MODULE = ModuleListModel.model_validate(module_list_model()).modules[0]


def _human_text() -> str:
    rows = calling_step_rows(_PROCEDURES, ROUTE, "create_reservation")
    messages = build_logic_messages(
        ROUTE, "create_reservation", _MODULE, rows, _FUNCTIONS, ["reservations"]
    )
    assert messages[0].content == LOGIC_SYSTEM_PROMPT
    return str(messages[1].content)


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_build_messages_and_convert_output():
    assert "## 対象の関数" in _human_text()
    output = LogicGenerationOutput(
        signature="def f()",
        args="なし",
        returns="なし",
        raises="なし",
        pre="前",
        post="後",
        pseudo=[GeneratedPseudoStep(text="する", sub=[])],
    )
    assert to_logic_draft(output).pseudo[0].text == "する"


# --- 呼ばれる手順の集め方 ---


def test_calling_step_rows_include_following_branches():
    rows = calling_step_rows(_PROCEDURES, ROUTE, "create_reservation")
    assert [(r.step_id, r.function_id) for r in rows] == [("F-01#1", "F-01")]
    assert [b.branch for b in rows[0].branches] == ["422"]


def test_calling_step_rows_are_empty_for_unknown_function():
    assert calling_step_rows(_PROCEDURES, ROUTE, "missing") == []


# --- プロンプト ---


def test_messages_carry_module_steps_branches_and_tables():
    text = _human_text()
    assert f"- モジュール: {ROUTE}" in text
    assert "F-01#1(F-01 " in text
    assert "分岐: 本文が不正 → 422" in text
    assert "- reservations" in text


def test_messages_mark_module_missing_from_list():
    messages = build_logic_messages(ROUTE, "f", None, [], _FUNCTIONS, [])
    assert "(モジュール一覧にありません)" in str(messages[1].content)
    assert "## 呼ばれる手順\n(ありません)" in str(messages[1].content)


def test_system_prompt_ends_with_naming_rules():
    assert LOGIC_SYSTEM_PROMPT.endswith(NAMING_RULES)


# --- E2E 用の固定の出力 ---


def test_e2e_fake_output_passes_stage6_validation():
    fake = _UML_OUTPUTS[LogicGenerationOutput]
    assert isinstance(fake, LogicGenerationOutput)
    key = logic_key(ROUTE, "create_reservation")
    model = LogicModel(logics=[LogicRow(module=ROUTE, function="create_reservation")])
    merged = merge_logic(model, key, to_logic_draft(fake))
    issues = validate_stage(
        6, merged.model_dump(mode="json"), StageSources(stages={5: procedure_model()})
    )
    assert issues == []
