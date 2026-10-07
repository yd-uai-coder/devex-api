"""段階8 実装手順書の検証(決定的な実装可能性チェック)のテスト。

SUT は`validate_procedure_doc`(`STAGE_VALIDATORS[8]`)と`validate_stage`、ドライバはこのテスト。
スタブ不要 ── 検証は model と入力の段階の内容(`StageSources`)だけから決まる純粋関数のため。
"""

from tests.fixtures.detailed_design import (
    document_stage_models,
    module_list_model,
    plan_model,
    procedure_doc_model,
    procedure_model,
)

from app.detailed_design import STAGE_VALIDATORS, StageIssue, StageSources, validate_stage
from app.detailed_design.validation import validate_procedure_doc


def _sources(overrides: dict[int, dict] | None = None) -> StageSources:
    return StageSources(stages=document_stage_models() | (overrides or {}))


def _codes(issues: list[StageIssue]) -> list[str]:
    return [issue.code for issue in issues]


def test_registered_and_fixture_passes() -> None:
    assert STAGE_VALIDATORS[8] is validate_procedure_doc
    assert validate_procedure_doc(procedure_doc_model(), _sources()) == []


def test_validated_without_model() -> None:
    """手順書がまだ無くても、段階7の単位と設計から指摘が出る(他の段階は内容が無ければ指摘なし)。"""
    sources = _sources({5: {"procedures": []}})

    issues = validate_stage(8, None, sources)

    assert _codes(issues) == ["NO_PROCEDURE"]
    assert validate_stage(7, None, sources) == []


def test_invalid_model() -> None:
    issues = validate_procedure_doc({"units": [{"title": "x"}]}, _sources())

    assert _codes(issues) == ["INVALID_MODEL"]
    assert issues[0].severity == "error"


def test_duplicate_unit_is_error_once() -> None:
    model = procedure_doc_model()
    model["units"] *= 3

    issues = validate_procedure_doc(model, _sources())

    assert [(i.code, i.severity, i.target, i.unit) for i in issues] == [
        ("DUPLICATE_UNIT", "error", "M-01-T02", "M-01-T02")
    ]


def test_unit_not_in_plan_is_mismatch() -> None:
    issues = validate_procedure_doc(procedure_doc_model(unit_id="M-02-T01"), _sources())

    assert [(i.code, i.severity, i.fix_stage) for i in issues] == [("UNIT_MISMATCH", "error", 8)]


def test_renamed_task_is_mismatch() -> None:
    """段階7を並べ替えると ID が同じでもタスク名が変わる。自動で付け替えず、作り直させる。"""
    issues = validate_procedure_doc(procedure_doc_model(title="開発環境を用意する"), _sources())

    assert _codes(issues) == ["UNIT_MISMATCH"]
    assert "予約を登録する" in issues[0].message


def test_unknown_module_file_is_major_warning() -> None:
    issues = validate_procedure_doc(procedure_doc_model(module="app/unknown.py"), _sources())

    issue = issues[0]
    assert (issue.code, issue.severity, issue.level, issue.fix_stage) == (
        "UNKNOWN_FILE",
        "warning",
        "major",
        4,
    )
    assert (issue.target, issue.unit) == ("app/unknown.py", "M-01-T02")


def test_missing_procedure_points_to_stage5() -> None:
    issues = validate_procedure_doc({}, _sources({5: {"procedures": []}}))

    issue = issues[0]
    assert (issue.code, issue.level, issue.fix_stage, issue.target, issue.unit) == (
        "NO_PROCEDURE",
        "major",
        5,
        "F-01",
        "M-01-T02",
    )


def test_db_step_without_crud_points_to_stage3() -> None:
    issues = validate_procedure_doc({}, _sources({3: {"cells": []}}))

    assert [(i.code, i.level, i.fix_stage, i.target) for i in issues] == [
        ("NOT_IN_CRUD", "major", 3, "F-01")
    ]


def _with_call(call: str) -> dict:
    """F-01 の手順の末尾に、app/api/routes/reservations.py の`call`を呼ぶ行(F-01#2)を足す。"""
    procedures = procedure_model()
    procedures["procedures"][0]["steps"].append(
        {"caller": "x", "callee": "app/api/routes/reservations.py", "call": call}
    )
    return procedures


def test_spelling_variant_of_stage6_function_is_minor() -> None:
    """段階6の関数と書き方だけが違う呼び出しは、吸収せず、手順の側(段階5)で段階6にそろえさせる。"""
    issues = validate_procedure_doc({}, _sources({5: _with_call("createReservation")}))

    assert [(i.code, i.level, i.fix_stage, i.target) for i in issues] == [
        ("UNRESOLVED_CALL", "minor", 5, "F-01#2")
    ]
    assert "「createReservation」は、段階6の「create_reservation」" in issues[0].message
    assert "段階5の手順の関数名を「create_reservation」にそろえます" in issues[0].message


def test_other_function_not_selected_in_stage6_is_not_reported() -> None:
    """段階6で詳しくしなかった同じモジュールの別の関数は、段階6が任意なので指摘しない。"""
    issues = validate_procedure_doc({}, _sources({5: _with_call("list_reservations")}))

    assert issues == []


def test_call_without_stage6_selection_is_not_reported() -> None:
    """段階6を飛ばした場合も、呼び出しは指摘しない。"""
    issues = validate_procedure_doc({}, _sources({6: {"logics": []}}))

    assert issues == []


def test_directory_module_is_major() -> None:
    sources = _sources(
        {
            4: {"modules": [{"path": "frontend"}, {"path": "app/api/routes/reservations.py"}]},
            7: plan_model(module="frontend"),
        }
    )

    issues = validate_procedure_doc({}, sources)

    assert [(i.code, i.level, i.fix_stage, i.target) for i in issues] == [
        ("MODULE_NOT_FILE", "major", 4, "frontend")
    ]


def test_stub_not_called_in_sequence_is_minor_warning_to_stage5() -> None:
    """スタブの欄が、手順の図で SUT から呼ばれないモジュール(手順に無い依存)を挙げている。"""
    repository = "app/repositories/reservation_repository.py"
    modules = module_list_model()
    modules["modules"].append({**modules["modules"][0], "path": repository, "functions": []})
    model = procedure_doc_model()
    model["units"][0]["tests"][0]["stub"] = "reservation_repository をフェイク"

    issues = validate_procedure_doc(model, _sources({4: modules}))

    issue = issues[0]
    assert (issue.code, issue.level, issue.fix_stage, issue.target, issue.unit) == (
        "STUB_OUTSIDE_SEQUENCE",
        "minor",
        5,
        "F-01",
        "M-01-T02",
    )
    assert repository in issue.message


def test_stub_naming_the_sut_or_unknown_sut_is_not_reported() -> None:
    sut_named = procedure_doc_model()
    sut_named["units"][0]["tests"][0]["stub"] = "reservations の外側だけをフェイク"
    unknown = procedure_doc_model()
    unknown["units"][0]["tests"][0].update(sut="無い関数", stub="reservations をフェイク")

    assert validate_procedure_doc(sut_named, _sources()) == []
    assert validate_procedure_doc(unknown, _sources()) == []
