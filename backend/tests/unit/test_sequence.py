"""段階5の手順から導くシーケンス図のテスト。

SUT: to_sequence / to_mermaid / reachable_callees / sut_participant / stubs_outside_sequence
     (app/detailed_design/sequence.py)、validate_procedures のシーケンスの指摘
     (app/detailed_design/validation.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。手順は小さな手作りの
`Procedure`を渡し、段階4の依存先は dict で渡す。
"""

from tests.fixtures.detailed_design import function_list_model, module_list_model

from app.detailed_design import (
    Procedure,
    ProcedureStep,
    SequenceMessage,
    SequenceNote,
    StageSources,
    reachable_callees,
    stubs_outside_sequence,
    sut_participant,
    to_mermaid,
    to_sequence,
)
from app.detailed_design.validation import validate_procedures

ROUTE = "a/route.py"
SERVICE = "a/service.py"
REPO = "a/repo.py"
DEPENDENCIES = {ROUTE: [SERVICE], SERVICE: [REPO], REPO: []}


def _step(caller: str, callee: str, call: str = "", **extra) -> ProcedureStep:
    return ProcedureStep(caller=caller, callee=callee, call=call, result=f"r-{call}", **extra)


def _branch(action: str, branch: str) -> ProcedureStep:
    return ProcedureStep(action=action, branch=branch, is_branch=True)


def _procedure(*steps: ProcedureStep) -> Procedure:
    return Procedure(function_id="F-01", steps=list(steps))


def _arrows(diagram) -> list[str]:
    """矢印を「元->先:手順ID」(戻りは -->、推測した戻りは末尾に *)で並べる。"""
    return [
        f"{e.source}{'-->' if e.kind == 'return' else '->'}{e.target}:{e.step_id}"
        + ("*" if e.derived else "")
        for e in diagram.events
        if isinstance(e, SequenceMessage)
    ]


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_nested_calls_infer_returns_from_inside():
    diagram = to_sequence(
        _procedure(
            _step("利用者", ROUTE, "post"),
            _step(ROUTE, SERVICE, "run"),
            _step(SERVICE, REPO, "save"),
        ),
        DEPENDENCIES,
    )

    assert [p.name for p in diagram.participants] == ["利用者", ROUTE, SERVICE, REPO]
    assert _arrows(diagram) == [
        "P1->P2:F-01#1",
        "P2->P3:F-01#2",
        "P3->P4:F-01#3",
        "P4-->P3:F-01#3*",
        "P3-->P2:F-01#2*",
        "P2-->P1:F-01#1*",
    ]
    assert diagram.issues == ()
    assert to_mermaid(diagram).startswith("sequenceDiagram\n")


# --- 導出 ---


def test_return_as_call_is_drawn_as_return_and_reported():
    diagram = to_sequence(
        _procedure(
            _step("利用者", ROUTE, "post"),
            _step(ROUTE, SERVICE, "run"),
            _step(SERVICE, "利用者", data="結果"),
        ),
        DEPENDENCIES,
    )

    assert _arrows(diagram)[-2:] == ["P3-->P2:F-01#2*", "P2-->P1:F-01#3"]
    assert [(i.step_id, i.code) for i in diagram.issues] == [("F-01#3", "RETURN_AS_CALL")]
    last = diagram.events[-1]
    assert isinstance(last, SequenceMessage) and last.label == "結果"


def test_explicit_return_kind_is_not_reported():
    diagram = to_sequence(
        _procedure(
            _step("利用者", ROUTE, "post"),
            _step(ROUTE, "利用者", data="201", kind="return"),
        ),
        DEPENDENCIES,
    )

    assert _arrows(diagram) == ["P1->P2:F-01#1", "P2-->P1:F-01#2"]
    assert diagram.issues == ()


def test_async_call_does_not_wait_for_return():
    diagram = to_sequence(
        _procedure(_step(ROUTE, SERVICE, "run"), _step(SERVICE, REPO, "job", kind="async")),
        DEPENDENCIES,
    )

    kinds = [e.kind for e in diagram.events if isinstance(e, SequenceMessage)]
    assert kinds == ["call", "async", "return"]


def test_self_call_is_a_loop_without_return():
    diagram = to_sequence(
        _procedure(
            _step(ROUTE, SERVICE, "run"),
            _step(SERVICE, SERVICE, "_build"),
            _step(SERVICE, REPO, "save"),
        ),
        DEPENDENCIES,
    )

    assert _arrows(diagram) == [
        "P1->P2:F-01#1",
        "P2->P2:F-01#2",
        "P2->P3:F-01#3",
        "P3-->P2:F-01#3*",
        "P2-->P1:F-01#1*",
    ]


def test_reports_missing_branch_target_dependency_and_unknown_nesting():
    diagram = to_sequence(
        _procedure(
            _step("利用者", ROUTE, "post", branch="3a へ"),
            _branch("不正", "422"),
            _step(ROUTE, REPO, "load"),
            _step(SERVICE, REPO, "save"),
        ),
        DEPENDENCIES,
    )

    assert [(i.step_id, i.code) for i in diagram.issues] == [
        ("F-01#1", "MISSING_BRANCH_TARGET"),
        ("F-01#2", "CALLEE_NOT_DEPENDENCY"),
        ("F-01#3", "NESTING_UNKNOWN"),
    ]
    note = next(e for e in diagram.events if isinstance(e, SequenceNote))
    assert (note.step_id, note.over, note.text) == ("F-01#1a", ("P1", "P2"), "不正 → 422")


def test_dependency_is_not_checked_without_stage4_or_for_actors_and_self_calls():
    procedure = _procedure(_step(ROUTE, REPO, "load"), _step(REPO, REPO, "inner"))
    assert to_sequence(procedure).issues == ()
    assert to_sequence(procedure, {ROUTE: ["a/repo"]}).issues == ()  # 区切り単位の部分一致
    assert to_sequence(_procedure(_step(ROUTE, "Gemini", "ask")), DEPENDENCIES).issues == ()


def test_empty_caller_is_reported_and_empty_callee_is_skipped():
    diagram = to_sequence(_procedure(_step("", ROUTE, "post"), _step("利用者", "")))

    assert [i.code for i in diagram.issues] == ["EMPTY_CALLER"]
    assert diagram.events == ()


# --- Mermaid ---


def test_mermaid_uses_aliases_step_numbers_and_strips_special_characters():
    diagram = to_sequence(
        _procedure(
            _step("利用者", ROUTE, "post", data="#id;本文"),
            _branch("不正", "422"),
            _step(ROUTE, SERVICE, "run"),
        ),
        DEPENDENCIES,
    )

    text = to_mermaid(diagram)

    assert "  participant P2 as a/route.py" in text
    assert "  P1->>P2: 1: post( id 本文)" in text
    assert "  Note over P1,P2: 1a 不正 → 422" in text
    assert "  P3-->>P2: (2 の戻り) r-run" in text
    assert "#" not in text.replace("sequenceDiagram", "")


# --- テスト観点との突き合わせ ---


def test_sut_and_stub_candidates_follow_calls_from_the_sut():
    diagram = to_sequence(
        _procedure(
            _step("利用者", ROUTE, "post"),
            _step(ROUTE, SERVICE, "Service.run"),
            _step(SERVICE, REPO, "save"),
        ),
        DEPENDENCIES,
    )

    service = sut_participant(diagram, "Service.run", "POST /x")
    route = sut_participant(diagram, "POST /x", "POST /x")

    assert (service, route) == (SERVICE, ROUTE)
    assert reachable_callees(diagram, SERVICE) == [REPO]
    assert reachable_callees(diagram, ROUTE) == [SERVICE, REPO]
    assert sut_participant(diagram, "無い関数", "POST /x") is None
    assert reachable_callees(diagram, "無い参加者") == []


def test_stubs_outside_sequence_returns_modules_not_called_from_the_sut():
    paths = [ROUTE, SERVICE, REPO, "a/project_repository.py"]

    assert stubs_outside_sequence("Repo・project_repository をフェイク", [REPO], paths) == [
        "a/project_repository.py"
    ]
    assert stubs_outside_sequence("repo だけをフェイク", [REPO], paths) == []


# --- 段階5の検証 ---


def test_stage5_validation_reports_sequence_issues_as_warnings():
    route = "app/api/routes/reservations.py"
    model = {
        "procedures": [
            {
                "function_id": "F-01",
                "reason": "検証",
                "steps": [
                    {"caller": "利用者", "callee": route, "call": "post", "branch": "2a へ"},
                    {"caller": route, "callee": "利用者", "data": "201"},
                ],
            }
        ]
    }
    sources = StageSources(stages={1: function_list_model(), 4: module_list_model()})

    issues = validate_procedures(model, sources)

    assert [(i.severity, i.code, i.target) for i in issues] == [
        ("warning", "MISSING_BRANCH_TARGET", "F-01#1"),
        ("warning", "RETURN_AS_CALL", "F-01#2"),
    ]
