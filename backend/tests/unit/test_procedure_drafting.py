"""段階5の下書きの入出力(プロンプト・出力スキーマ・手順への変換)のテスト。

SUT: build_procedure_messages / to_procedure_draft(app/detailed_design/procedure_drafting.py)、
     E2E 用の固定の出力(app/ai/llm/fake.py)、merge_procedure の分岐の参照の付け直し
     (app/detailed_design/procedure.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、LLM を呼ばないため(メッセージを組み立て、
構造化出力を受け取って変換するだけ。LLM の呼び出しはサービス層の責務)。
"""

from tests.fixtures.detailed_design import function_list_model, module_list_model

from app.ai.llm.fake import _UML_OUTPUTS
from app.detailed_design import (
    CrudCell,
    DfdAccess,
    FunctionListModel,
    ModuleListModel,
    ProcedureModel,
    ProcessSummaryRow,
    StageSources,
    merge_procedure,
    validate_stage,
)
from app.detailed_design.procedure_drafting import (
    PROCEDURE_SYSTEM_PROMPT,
    GeneratedBranch,
    GeneratedStep,
    ProcedureGenerationOutput,
    build_procedure_messages,
    to_procedure_draft,
)
from app.detailed_design.prompt_rules import NAMING_RULES
from app.detailed_design.sequence import to_sequence
from app.detailed_design.structure_drafting import ModuleListGenerationOutput

_FUNCTION = FunctionListModel.model_validate(function_list_model()).functions[0]
_MODULES = ModuleListModel.model_validate(
    {
        "modules": [
            *module_list_model()["modules"],
            {"path": "app/main.py", "layer": "api", "all_functions": True},
        ]
    }
).modules


def _human_text() -> str:
    messages = build_procedure_messages(
        _FUNCTION,
        ProcessSummaryRow(function_id="F-01", input="予約", process="保存する", output="予約"),
        [
            DfdAccess(function_id="F-01", table="reservations", kind="write"),
            DfdAccess(function_id="F-02", table="equipments", kind="read"),
        ],
        [
            CrudCell(function_id="F-01", table="reservations", ops="C"),
            CrudCell(function_id="F-02", table="equipments", ops="R"),
        ],
        ["reservations", "equipments"],
        _MODULES,
    )
    assert messages[0].content == PROCEDURE_SYSTEM_PROMPT
    return str(messages[1].content)


def test_smoke_build_messages_and_convert_output():
    output = ProcedureGenerationOutput(
        reason="理由",
        note="",
        steps=[
            GeneratedStep(
                caller="利用者",
                callee="routes/reservations",
                call="f",
                data="d",
                action="a",
                result="r",
                db="—",
            )
        ],
    )
    draft = to_procedure_draft(output)
    assert draft.reason == "理由"
    assert draft.steps[0].callee == "routes/reservations"
    assert "F-01 予約を登録する" in _human_text()


def test_system_prompt_requires_module_paths_and_ends_with_naming_rules():
    assert "【モジュール一覧】のパスを一字一句そのまま" in PROCEDURE_SYSTEM_PROMPT
    assert "手順番号は書かない" in PROCEDURE_SYSTEM_PROMPT
    assert PROCEDURE_SYSTEM_PROMPT.endswith(NAMING_RULES)


def test_messages_pass_only_target_function_accesses_and_crud():
    text = _human_text()
    assert "- reservations: 書き込み" in text
    assert "- reservations: C" in text
    assert "equipments: 読み" not in text
    assert "equipments: R" not in text
    assert "- equipments" in text  # テーブル名は全部渡す


def test_messages_list_modules_with_all_functions_mark():
    text = _human_text()
    assert "- app/api/routes/reservations.py(層: api" in text
    assert "関わる処理: 全処理" in text


def test_messages_without_summary_say_none():
    messages = build_procedure_messages(_FUNCTION, None, [], [], [], [])
    assert "## 処理概要表\n(ありません)" in str(messages[1].content)


def test_e2e_fake_output_passes_stage5_validation_with_fake_module_list():
    fake_modules = _UML_OUTPUTS[ModuleListGenerationOutput]
    assert isinstance(fake_modules, ModuleListGenerationOutput)
    paths = [m.path for m in fake_modules.modules]
    output = _UML_OUTPUTS[ProcedureGenerationOutput]
    assert isinstance(output, ProcedureGenerationOutput)

    model = merge_procedure(ProcedureModel(), "F-01", to_procedure_draft(output), paths)

    modules = {"modules": [m.model_dump() for m in fake_modules.modules]}
    sources = StageSources(stages={1: function_list_model(), 4: modules})
    assert validate_stage(5, model.model_dump(), sources) == []


def test_kind_is_passed_to_draft_and_defaults_to_call():
    back = GeneratedStep(
        caller="routes/reservations",
        callee="利用者",
        call="",
        data="予約",
        action="返す",
        result="",
        db="—",
        kind="return",
    )
    plain = back.model_copy(update={"kind": "call"})
    output = ProcedureGenerationOutput(reason="r", note="", steps=[back, plain])

    assert [s.kind for s in to_procedure_draft(output).steps] == ["return", "call"]
    assert "kind は呼び出しなら call" in PROCEDURE_SYSTEM_PROMPT
    assert GeneratedStep.model_fields["kind"].default == "call"


def _step(action: str, *branches: tuple[str, str]) -> GeneratedStep:
    return GeneratedStep(
        caller="利用者",
        callee="app/api/routes/reservations.py",
        call="create_reservation",
        data="d",
        action=action,
        result="r",
        db="—",
        branches=[GeneratedBranch(condition=c, result=r) for c, r in branches],
    )


def test_nested_branches_become_rows_right_after_their_step():
    """入れ子の分岐は、元の手順の直後の分岐の行(条件 = 処理内容、結果 = 分岐の欄)になる。"""
    output = ProcedureGenerationOutput(
        reason="r",
        note="",
        steps=[
            _step("検証する", ("不正", "422")),
            _step("保存する", ("重複", "409"), ("無い", "404")),
        ],
    )
    steps = to_procedure_draft(output).steps
    assert [(s.action, s.branch, s.is_branch) for s in steps] == [
        ("検証する", "", False),
        ("不正", "422", True),
        ("保存する", "", False),
        ("重複", "409", True),
        ("無い", "404", True),
    ]
    assert steps[1].caller == steps[1].callee == steps[1].call == ""
    assert "branches に、条件(condition)と結果(result)の組" in PROCEDURE_SYSTEM_PROMPT
    assert "is_branch" not in GeneratedStep.model_fields


def test_merged_draft_links_branch_refs_without_missing_targets():
    """merge_procedure が参照を並びから振るので、シーケンス図に MISSING_BRANCH_TARGET が出ない。"""
    output = ProcedureGenerationOutput(
        reason="r",
        note="",
        steps=[
            _step("検証する", ("不正", "422")),
            _step("保存する", ("重複", "409"), ("無い", "404")),
        ],
    )
    paths = [m.path for m in _MODULES]
    model = merge_procedure(ProcedureModel(), "F-01", to_procedure_draft(output), paths)
    procedure = model.procedures[0]

    assert [s.branch for s in procedure.steps] == ["1a へ", "422", "2a へ、2b へ", "409", "404"]
    issues = to_sequence(procedure).issues
    assert [i.code for i in issues if i.code == "MISSING_BRANCH_TARGET"] == []
