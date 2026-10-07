"""詳細設計書の表の導出のテスト。

SUT: anchor / functions_by_id / logic_ids / procedure_steps / linked_logic_ids / main_step_count /
     step_kind_label /
     logic_views / involvement / data_item_usage / crud_matrix
     (app/detailed_design/document/views.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。
"""

import uuid

from tests.fixtures.detailed_design import document_stage_models, er_model, group_dfd_model

from app.detailed_design import (
    CrudModel,
    FunctionListModel,
    LogicModel,
    ModuleListModel,
    Procedure,
    ProcedureModel,
    ProcedureStep,
)
from app.detailed_design.document import (
    CrudMark,
    anchor,
    crud_matrix,
    data_item_usage,
    involvement,
    linked_logic_ids,
    logic_ids,
    logic_views,
    procedure_steps,
)
from app.detailed_design.document.views import functions_by_id, main_step_count, step_kind_label
from app.uml.domain.er import ErSemanticModel

MODELS = document_stage_models()
PROCEDURES = ProcedureModel.model_validate(MODELS[5])
LOGICS = LogicModel.model_validate(MODELS[6])
DATA_ITEM = "11111111-1111-1111-1111-111111111111"


def test_anchor_replaces_hash_and_lowercases() -> None:
    assert anchor("F-01#4a") == "f-01-4a"
    assert anchor("L-02") == "l-02"


def test_functions_by_id_is_empty_when_stage1_is_unapproved() -> None:
    assert functions_by_id(None) == {}
    rows = functions_by_id(FunctionListModel.model_validate(MODELS[1]))
    assert rows["F-01"].name == "予約を登録する"


def test_procedure_steps_number_rows_and_link_to_logic_by_callee_and_call() -> None:
    [procedure] = PROCEDURES.procedures

    steps = procedure_steps(procedure, logic_ids(LOGICS))

    assert [(s.number, s.step_id, s.logic_id) for s in steps] == [
        ("1", "F-01#1", "L-01"),
        ("1a", "F-01#1a", None),  # 分岐の行は関数を呼ばない
    ]
    assert linked_logic_ids(steps) == ["L-01"]
    assert main_step_count(procedure) == 1


def test_procedure_steps_have_no_logic_when_stage6_is_not_approved() -> None:
    [procedure] = PROCEDURES.procedures

    steps = procedure_steps(procedure, logic_ids(None))

    assert all(s.logic_id is None for s in steps)


def test_step_kind_label_is_empty_for_branch_rows() -> None:
    assert step_kind_label(ProcedureStep(kind="async")) == "非同期"
    assert step_kind_label(ProcedureStep(kind="return")) == "戻り"
    assert step_kind_label(ProcedureStep(is_branch=True)) == ""


def test_involvement_skips_return_rows() -> None:
    model = ProcedureModel(
        procedures=[
            Procedure(
                function_id="F-01",
                steps=[
                    ProcedureStep(caller="利用者", callee="app/a.py", call="f"),
                    ProcedureStep(caller="app/b.py", callee="app/a.py", kind="return"),
                ],
            )
        ]
    )
    assert involvement(model, None).cells == {"F-01": {"app/a.py": ["1"]}}


def test_logic_views_derive_calling_steps() -> None:
    [view] = logic_views(LOGICS, PROCEDURES)

    assert view.logic_id == "L-01"
    assert view.row.function == "create_reservation"
    assert view.step_ids == ("F-01#1",)
    assert logic_views(None, PROCEDURES) == []


def test_involvement_columns_follow_module_list_and_skip_external_actors() -> None:
    modules = ModuleListModel.model_validate(
        {"modules": [{"path": "app/services/x.py"}, {"path": "app/api/routes/reservations.py"}]}
    )

    table = involvement(PROCEDURES, modules)

    assert table.modules == ["app/api/routes/reservations.py"]  # 呼ばれたモジュールだけ
    assert table.cells == {"F-01": {"app/api/routes/reservations.py": ["1"]}}


def test_data_item_usage_collects_processes_on_flows() -> None:
    model = group_dfd_model(uuid.UUID(DATA_ITEM))

    assert data_item_usage([model]) == {DATA_ITEM: ["F-01"]}


def test_crud_matrix_marks_ops_by_how_they_are_decided() -> None:
    dfd = group_dfd_model(uuid.UUID(DATA_ITEM))  # F-01 → reservations(書き込み)
    crud = CrudModel.model_validate(
        {
            "cells": [
                {"function_id": "F-01", "table": "reservations", "ops": "CR"},
                {"function_id": "F-02", "table": "users", "ops": "R"},
            ]
        }
    )

    matrix = crud_matrix(crud, None, [dfd], ["F-02", "F-01"])

    assert matrix.tables == ["reservations", "users"]
    assert [fid for fid, _ in matrix.rows] == ["F-02", "F-01"]
    ops = dict(matrix.rows)
    assert ops["F-01"]["reservations"] == [CrudMark("C", "dfd_write"), CrudMark("R", "human")]
    assert ops["F-02"]["users"] == [CrudMark("R", "human")]


def test_crud_matrix_columns_follow_er_table_order() -> None:
    er = ErSemanticModel.model_validate(er_model(tables=("users", "reservations")))
    crud = CrudModel.model_validate(MODELS[3])

    matrix = crud_matrix(crud, er, [])

    assert matrix.tables == ["users", "reservations"]
