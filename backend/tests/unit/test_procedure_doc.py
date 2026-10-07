"""段階8 実装手順書の意味モデルと、単位が参照する設計の導出(純粋関数)のテスト。

SUT は`app/detailed_design/procedure_doc.py`の純粋関数、ドライバはこのテスト。
スタブ不要 ── 対象は入力の段階の内容(辞書)だけから決まり、DB や LLM を呼ばないため。
"""

from tests.fixtures.detailed_design import (
    crud_model,
    logic_model,
    module_list_model,
    plan_model,
    procedure_doc_model,
    procedure_model,
)

from app.detailed_design import (
    PROCEDURE_DOC_STAGE,
    DesignRef,
    PlanModel,
    PlanTask,
    ProcedureDocModel,
    design_index,
    plan_units,
    unit_refs,
)
from app.detailed_design.procedure_doc import name_key, spelling_match


def _stages(overrides: dict[int, dict] | None = None) -> dict[int, dict]:
    """段階3〜6の承認済みの内容(`overrides`で段階ごとに差し替える)。"""
    stages = {
        3: crud_model(),
        4: module_list_model(),
        5: procedure_model(),
        6: logic_model(),
    }
    return stages | (overrides or {})


def test_model_reads_fixture_with_defaults() -> None:
    model = ProcedureDocModel.model_validate(procedure_doc_model())

    unit = model.units[0]
    assert PROCEDURE_DOC_STAGE == 8
    assert unit.unit_id == "M-01-T02"
    assert [f.kind for f in unit.files] == ["module", "test"]
    assert unit.files[1].responsibility == ""
    assert unit.findings[0].level == "critical"
    assert unit.findings[0].fix_stage == 7


def test_plan_units_follow_plan_order() -> None:
    units = plan_units(PlanModel.model_validate(plan_model()))

    assert [(u.unit_id, u.milestone, u.task.title) for u in units] == [
        ("M-01-T01", "M-01", "開発環境を用意する"),
        ("M-01-T02", "M-01", "予約を登録する"),
    ]


def test_design_index_keeps_only_drafted_and_non_empty() -> None:
    procedures = procedure_model()
    procedures["procedures"].append({"function_id": "F-02", "steps": []})
    logics = logic_model()
    logics["logics"].append({"module": "app/services/x.py", "function": "y"})

    index = design_index(_stages({5: procedures, 6: logics}))

    assert set(index.procedures) == {"F-01"}
    assert index.logic_keys == {"app/api/routes/reservations.py::create_reservation"}
    assert index.logic_functions == {
        "app/api/routes/reservations.py": ("create_reservation",),
        "app/services/x.py": ("y",),
    }
    assert index.module_paths == {"app/api/routes/reservations.py"}
    assert index.crud_functions == {"F-01"}


def test_design_index_of_missing_stages_is_empty() -> None:
    index = design_index({})

    assert not index.procedures
    assert not index.module_paths


def test_unit_refs_derive_procedure_logic_and_module() -> None:
    task = PlanTask(function_ids=["F-01"], modules=["app/api/routes/reservations.py"])

    refs = unit_refs(task, design_index(_stages()))

    assert refs == [
        DesignRef("procedure", "F-01", True),
        DesignRef("logic", "app/api/routes/reservations.py::create_reservation", True, "F-01#1"),
        DesignRef("module", "app/api/routes/reservations.py", True),
    ]


def test_unit_refs_mark_unresolved() -> None:
    task = PlanTask(function_ids=["F-09"], modules=["app/unknown.py"])

    refs = unit_refs(task, design_index(_stages({6: {"logics": []}})))

    assert refs == [
        DesignRef("procedure", "F-09", False),
        DesignRef("module", "app/unknown.py", False),
    ]


def test_unit_refs_of_undrafted_logic_are_unresolved() -> None:
    task = PlanTask(function_ids=["F-01"])

    refs = unit_refs(task, design_index(_stages({6: {"logics": []}})))

    assert refs[1] == DesignRef(
        "logic", "app/api/routes/reservations.py::create_reservation", False, "F-01#1"
    )


def test_name_key_ignores_case_and_separators() -> None:
    assert name_key("createReservation") == name_key(" create_reservation ") == "createreservation"
    assert name_key("create-reservation") == "createreservation"
    assert name_key("list_reservations") != name_key("create_reservation")


def test_spelling_match_finds_only_same_module_variant() -> None:
    index = design_index(_stages())
    route = "app/api/routes/reservations.py"

    assert spelling_match(index, route, "createReservation") == "create_reservation"
    assert spelling_match(index, route, "create_reservation") is None
    assert spelling_match(index, route, "list_reservations") is None
    assert spelling_match(index, "app/services/other.py", "createReservation") is None
