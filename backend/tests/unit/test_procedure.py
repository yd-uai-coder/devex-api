"""段階5 主要処理の手順の組み立てと検証のテスト。

SUT: number_steps / step_id / is_external_actor / calls_function / resolve_callee /
     merge_procedure / pending_function_ids(app/detailed_design/procedure.py)、
     validate_procedures / STAGE_VALIDATORS(app/detailed_design/validation.py)、
     パッケージの re-export(app/detailed_design/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。段階1・4の内容は DB から
読まず、`StageSources`に dict で渡す(読み取りはサービス層の責務)。
"""

import pytest
from tests.fixtures.detailed_design import (
    function_list_model,
    module_list_model,
    procedure_model,
)

from app.detailed_design import (
    MAX_PROCEDURE_TARGETS,
    PROCEDURE_STAGE,
    STAGE_VALIDATORS,
    Procedure,
    ProcedureDraft,
    ProcedureModel,
    ProcedureStep,
    StageSources,
    calls_function,
    has_errors,
    is_external_actor,
    merge_procedure,
    number_steps,
    pending_function_ids,
    resolve_callee,
    step_id,
    validate_stage,
)
from app.detailed_design.validation import validate_procedures

ROUTE = "app/api/routes/reservations.py"
PATHS = [ROUTE, "app/services/reservation.py", "app/repositories/{reservation,equipment}.py"]


def _sources() -> StageSources:
    return StageSources(stages={1: function_list_model(), 4: module_list_model()})


def _codes(model: dict) -> list[str]:
    return [issue.code for issue in validate_procedures(model, _sources())]


def _main(callee: str = ROUTE, call: str = "f") -> ProcedureStep:
    return ProcedureStep(caller="利用者", callee=callee, call=call, action="処理する")


def _branch() -> ProcedureStep:
    return ProcedureStep(action="不正", branch="400", is_branch=True)


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_fixture_model_passes_stage5_validation():
    assert PROCEDURE_STAGE == 5
    assert MAX_PROCEDURE_TARGETS == 5
    assert STAGE_VALIDATORS[PROCEDURE_STAGE] is validate_procedures
    assert validate_stage(PROCEDURE_STAGE, procedure_model(), _sources()) == []


# --- 手順番号・手順ID ---


def test_number_steps_numbers_main_rows_and_letters_branches():
    steps = [_main(), _branch(), _branch(), _main(), _main(), _branch()]
    assert number_steps(steps) == ["1", "1a", "1b", "2", "3", "3a"]


def test_number_steps_marks_leading_branch_as_zero():
    assert number_steps([_branch(), _main()]) == ["0a", "1"]


def test_number_steps_uses_two_letters_after_z():
    numbers = number_steps([_main(), *[_branch() for _ in range(27)]])
    assert numbers[26] == "1z"
    assert numbers[27] == "1aa"


def test_step_id_joins_function_id_and_number():
    assert step_id("F-01", "4a") == "F-01#4a"


# --- 呼び出し先 ---


@pytest.mark.parametrize(("callee", "expected"), [("利用者", True), (ROUTE, False)])
def test_is_external_actor_is_names_without_slash(callee, expected):
    assert is_external_actor(callee) is expected


@pytest.mark.parametrize(
    ("callee", "expected"),
    [
        (ROUTE, ROUTE),  # 完全一致
        ("services/reservation", "app/services/reservation.py"),  # 短い書き方
        ("app/repositories/equipment.py", "app/repositories/{reservation,equipment}.py"),
        (" 利用者 ", "利用者"),  # 外部の役者
        ("app/routes/missing.py", "app/routes/missing.py"),  # 当たらない → そのまま
        ("app", "app"),  # 「/」が無いので外部の役者の扱い
    ],
)
def test_resolve_callee_aligns_to_module_path(callee, expected):
    assert resolve_callee(callee, PATHS) == expected


def test_resolve_callee_keeps_ambiguous_reference():
    # app/services と app/repositories/… の両方に当たる書き方は決めずに残す
    paths = ["app/x/reservation.py", "app/y/reservation.py"]
    assert resolve_callee("reservation/", paths) == "reservation/"
    assert resolve_callee("app/x", ["app/x/a.py", "app/x/b.py"]) == "app/x"


# --- 下書きの取り込み ---


def test_merge_procedure_replaces_only_target_and_keeps_human_reason():
    other = Procedure(function_id="F-02", reason="人", steps=[_main()])
    model = ProcedureModel(procedures=[Procedure(function_id="F-01", reason="人の理由"), other])
    draft = ProcedureDraft(
        reason="AIの理由",
        note=" 手順 1〜2 が1つのトランザクション ",
        steps=(_main("services/reservation"), _branch()),
    )

    merged = merge_procedure(model, "F-01", draft, PATHS)

    first, second = merged.procedures
    assert first.reason == "人の理由"
    assert first.note == "手順 1〜2 が1つのトランザクション"
    assert first.steps[0].callee == "app/services/reservation.py"
    assert first.steps[1].is_branch
    assert second == other


def test_merge_procedure_uses_draft_reason_when_human_left_it_empty():
    model = ProcedureModel(procedures=[Procedure(function_id="F-01")])
    draft = ProcedureDraft(reason="AIの理由", note="", steps=(_main(),))
    assert merge_procedure(model, "F-01", draft, PATHS).procedures[0].reason == "AIの理由"


def test_merge_procedure_drops_leading_branch_and_empty_rows_and_clears_branch_calls():
    branch = ProcedureStep(caller="x", callee=ROUTE, call="f", action="不正", is_branch=True)
    draft = ProcedureDraft(reason="", note="", steps=(branch, ProcedureStep(), _main(), branch))

    steps = merge_procedure(ProcedureModel(), "F-01", draft, PATHS).procedures[0].steps

    assert [s.is_branch for s in steps] == [False, True]
    assert (steps[1].caller, steps[1].callee, steps[1].call) == ("", "", "")


def test_step_without_kind_is_read_as_sync_call():
    # 種別の欄より前に保存した行は、同期の呼び出しとして読む
    assert ProcedureStep.model_validate({"caller": "利用者", "callee": ROUTE}).kind == "call"


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        (ProcedureStep(callee=ROUTE, call="f"), True),
        (ProcedureStep(callee=ROUTE, call="f", kind="async"), True),
        (ProcedureStep(callee=ROUTE, call="f", kind="return"), False),
        (ProcedureStep(callee=ROUTE, call="f", is_branch=True), False),
        (ProcedureStep(callee="利用者", call="f"), False),
        (ProcedureStep(callee=ROUTE, call=" "), False),
    ],
)
def test_calls_function_only_for_module_calls(step, expected):
    assert calls_function(step) is expected


def test_merge_procedure_keeps_kind_and_resets_branch_kind():
    back = ProcedureStep(caller=ROUTE, callee="利用者", action="返す", kind="return")
    branch = ProcedureStep(action="不正", branch="400", is_branch=True, kind="return")
    draft = ProcedureDraft(reason="", note="", steps=(_main(), branch, back))

    steps = merge_procedure(ProcedureModel(), "F-01", draft, PATHS).procedures[0].steps

    assert [s.kind for s in steps] == ["call", "call", "return"]


def test_pending_function_ids_lists_procedures_without_steps_in_order():
    model = ProcedureModel(
        procedures=[
            Procedure(function_id="F-03"),
            Procedure(function_id="F-01", steps=[_main()]),
            Procedure(function_id="F-02"),
        ]
    )
    assert pending_function_ids(model) == ["F-03", "F-02"]


# --- 検証 ---


def test_validate_rejects_invalid_shape():
    assert _codes({"procedures": "x"}) == ["INVALID_MODEL"]


def test_validate_requires_at_least_one_procedure():
    assert _codes({"procedures": []}) == ["EMPTY_PROCEDURES"]


def test_validate_reports_unknown_and_duplicate_function():
    model = procedure_model()
    model["procedures"].append({**model["procedures"][0]})
    model["procedures"].append({**model["procedures"][0], "function_id": "F-99"})
    codes = _codes(model)
    assert "DUPLICATE_PROCEDURE" in codes
    assert "UNKNOWN_FUNCTION" in codes


def test_validate_requires_steps_and_warns_empty_reason():
    model = {"procedures": [{"function_id": "F-01", "steps": []}]}
    assert _codes(model) == ["EMPTY_REASON", "EMPTY_STEPS"]
    assert has_errors(validate_procedures(model, _sources()))


def test_validate_rejects_leading_branch():
    model = procedure_model()
    model["procedures"][0]["steps"].reverse()
    assert "LEADING_BRANCH" in _codes(model)


def test_validate_rejects_callee_not_in_module_list_with_step_id_target():
    issues = validate_procedures(procedure_model(callee="app/routes/missing.py"), _sources())
    assert [(i.severity, i.code, i.target) for i in issues] == [
        ("error", "UNKNOWN_CALLEE", "F-01#1")
    ]


def test_validate_rejects_short_callee_that_was_not_aligned():
    # 区切り単位で当たっても、保存された呼び出し先はパスそのものでなければならない(関与表の鍵)
    assert _codes(procedure_model(callee="routes/reservations")) == ["UNKNOWN_CALLEE"]


def test_validate_rejects_empty_callee_and_allows_external_actor():
    assert _codes(procedure_model(callee="")) == ["EMPTY_CALLEE"]
    assert _codes(procedure_model(callee="利用者")) == []


def test_validate_warns_module_call_without_function():
    model = procedure_model()
    model["procedures"][0]["steps"][0]["call"] = " "
    issues = validate_procedures(model, _sources())
    assert [(i.severity, i.code) for i in issues] == [("warning", "EMPTY_CALL")]


def test_validate_does_not_warn_missing_function_on_return_row():
    model = procedure_model()
    model["procedures"][0]["steps"].append(
        {"caller": "app/api/routes/reservations.py", "callee": "利用者", "kind": "return"}
    )
    model["procedures"][0]["steps"].append(
        {"caller": "利用者", "callee": "app/api/routes/reservations.py", "kind": "return"}
    )
    assert "EMPTY_CALL" not in _codes(model)
