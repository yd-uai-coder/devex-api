"""段階6 処理ロジックの詳細の組み立てと検証のテスト。

SUT: logic_id / logic_key / is_drafted / logic_candidates / calling_steps / merge_logic /
     pending_logic_keys / generation_targets(app/detailed_design/logic.py)、
     validate_logics / STAGE_VALIDATORS(app/detailed_design/validation.py)、
     パッケージの re-export(app/detailed_design/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。段階5の手順は DB から読まず、
`StageSources`に dict で渡す(読み取りはサービス層の責務)。
"""

from tests.fixtures.detailed_design import logic_model, procedure_model

from app.detailed_design import (
    LOGIC_STAGE,
    MAX_LOGIC_TARGETS,
    STAGE_VALIDATORS,
    LogicDraft,
    LogicModel,
    LogicRow,
    ProcedureModel,
    ProcedureStep,
    PseudoStep,
    StageSources,
    calling_steps,
    has_errors,
    is_drafted,
    logic_candidates,
    logic_id,
    logic_key,
    merge_logic,
    pending_logic_keys,
    validate_stage,
)
from app.detailed_design.logic import generation_targets
from app.detailed_design.procedure import Procedure
from app.detailed_design.validation import validate_logics

ROUTE = "app/api/routes/reservations.py"
SERVICE = "app/services/reservation.py"


def _sources() -> StageSources:
    return StageSources(stages={5: procedure_model()})


def _codes(model: dict) -> list[str]:
    return [issue.code for issue in validate_logics(model, _sources())]


def _procedures() -> ProcedureModel:
    """F-01 と F-02 が同じサービスの関数を呼び、F-02 は分岐・外部の役者・関数の空の行も持つ。"""
    return ProcedureModel(
        procedures=[
            Procedure(
                function_id="F-01",
                steps=[
                    ProcedureStep(caller="利用者", callee=ROUTE, call="create_reservation"),
                    ProcedureStep(action="不正", branch="422", is_branch=True),
                    ProcedureStep(caller=ROUTE, callee=SERVICE, call="ReservationService.create"),
                ],
            ),
            Procedure(
                function_id="F-02",
                steps=[
                    ProcedureStep(caller="利用者", callee=ROUTE, call=""),
                    ProcedureStep(caller=ROUTE, callee=SERVICE, call="ReservationService.create"),
                    ProcedureStep(caller=ROUTE, callee="利用者", call="respond"),
                ],
            ),
        ]
    )


def _draft(signature: str = "def f() -> None") -> LogicDraft:
    return LogicDraft(
        signature=f" {signature} ",
        args="なし",
        returns="なし",
        raises="なし",
        pre="前",
        post="後",
        pseudo=(PseudoStep(text=" 確かめる ", sub=[" 不正なら 400 ", " "]), PseudoStep()),
    )


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_fixture_model_passes_stage6_validation():
    issues = validate_stage(LOGIC_STAGE, logic_model(), _sources())
    assert not has_errors(issues)
    assert STAGE_VALIDATORS[LOGIC_STAGE] is validate_logics
    assert MAX_LOGIC_TARGETS == 5


# --- L-ID・鍵・下書きの有無 ---


def test_logic_id_numbers_from_order():
    assert [logic_id(i) for i in (0, 1, 9, 99)] == ["L-01", "L-02", "L-10", "L-100"]


def test_logic_key_joins_trimmed_module_and_function():
    assert logic_key(f" {SERVICE} ", " create ") == f"{SERVICE}::create"


def test_is_drafted_needs_signature_or_pseudo():
    assert not is_drafted(LogicRow(module=SERVICE, function="f"))
    assert is_drafted(LogicRow(module=SERVICE, function="f", signature="def f()"))
    assert is_drafted(LogicRow(module=SERVICE, function="f", pseudo=[PseudoStep(text="a")]))


# --- 候補と呼ばれる手順(05 → 06 の紐づけを導く) ---


def test_logic_candidates_group_steps_by_callee_and_call():
    candidates = logic_candidates(_procedures())
    assert [(c.module, c.function, c.step_ids) for c in candidates] == [
        (ROUTE, "create_reservation", ("F-01#1",)),
        (SERVICE, "ReservationService.create", ("F-01#2", "F-02#2")),
    ]


def test_logic_candidates_skip_branch_external_actor_and_empty_call():
    functions = {c.function for c in logic_candidates(_procedures())}
    assert "respond" not in functions  # 呼び出し先が外部の役者
    assert "" not in functions  # 関数が空


def test_calling_steps_returns_step_ids_or_empty():
    assert calling_steps(_procedures(), SERVICE, "ReservationService.create") == [
        "F-01#2",
        "F-02#2",
    ]
    assert calling_steps(_procedures(), SERVICE, "missing") == []


# --- 1つの関数の置き換え ---


def test_merge_logic_replaces_only_target_and_normalizes_pseudo():
    model = LogicModel(
        logics=[
            LogicRow(module=ROUTE, function="create_reservation", signature="人の手直し"),
            LogicRow(module=SERVICE, function="ReservationService.create"),
        ]
    )
    merged = merge_logic(model, logic_key(SERVICE, "ReservationService.create"), _draft())
    assert merged.logics[0].signature == "人の手直し"
    target = merged.logics[1]
    assert target.signature == "def f() -> None"
    assert [(p.text, p.sub) for p in target.pseudo] == [("確かめる", ["不正なら 400"])]


def test_merge_logic_ignores_unknown_key():
    model = LogicModel(logics=[LogicRow(module=ROUTE, function="f")])
    assert merge_logic(model, logic_key(SERVICE, "g"), _draft()) == model


def test_pending_logic_keys_and_generation_targets():
    model = LogicModel(
        logics=[
            LogicRow(module=ROUTE, function="a", signature="def a()"),
            LogicRow(module=SERVICE, function="b"),
        ]
    )
    assert pending_logic_keys(model) == [logic_key(SERVICE, "b")]
    assert generation_targets(model, None) == [logic_key(SERVICE, "b")]
    requested = [(ROUTE, "a"), (ROUTE, "a"), (" ", " ")]
    assert generation_targets(model, requested) == [logic_key(ROUTE, "a")]


# --- 段階6の検証 ---


def test_validate_rejects_invalid_shape():
    assert _codes({"logics": [{"module": 1}]}) == ["INVALID_MODEL"]


def test_validate_allows_zero_logics_to_skip_stage6():
    assert _codes({"logics": []}) == []


def test_validate_rejects_empty_key_and_duplicate():
    duplicated = logic_model()["logics"] * 2
    assert _codes({"logics": duplicated}).count("DUPLICATE_LOGIC") == 2
    assert _codes(logic_model(function=" ")) == ["EMPTY_LOGIC_KEY"]


def test_validate_rejects_function_not_called_by_stage5():
    assert "UNCALLED_LOGIC" in _codes(logic_model(function="missing"))


def test_validate_rejects_undrafted_logic_with_logic_id_target():
    model = {"logics": [{"module": ROUTE, "function": "create_reservation"}]}
    issues = validate_logics(model, _sources())
    assert [(i.code, i.target) for i in issues] == [("EMPTY_LOGIC", "L-01")]


def test_validate_warns_empty_condition():
    issues = validate_logics(logic_model(pre=""), _sources())
    assert [(i.severity, i.code) for i in issues] == [("warning", "EMPTY_CONDITION")]
