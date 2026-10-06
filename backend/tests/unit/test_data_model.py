"""段階3 データモデルの組み立て(CRUD 図)と検証のテスト。

SUT: dfd_accesses / merge_crud / confirm_drafts / normalize_ops / table_key / er_table_names /
     tables_without_primary_key(app/detailed_design/data_model.py)、
     validate_data_model / selected_dfd_accesses / STAGE_VALIDATORS
     (app/detailed_design/validation.py)、
     ErColumn・ErElement の制約・説明(app/uml/domain/er.py)、
     パッケージの re-export(app/detailed_design/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。DFD・ER は DB から読まず、
意味モデルの dict と`StageSources`の要約を渡す(読み取りはサービス層の責務)。
"""

from tests.fixtures.detailed_design import (
    crud_model,
    data_flow_model,
    er_model,
    function_list_model,
)

from app.detailed_design import (
    CRUD_OPS,
    DATA_MODEL_STAGE,
    ER_SUBJECT,
    STAGE_VALIDATORS,
    CrudDraft,
    CrudModel,
    DfdAccess,
    DfdDiagramSummary,
    ErDiagramSummary,
    FunctionListModel,
    StageSources,
    confirm_drafts,
    dfd_accesses,
    dfd_subject,
    er_table_names,
    has_errors,
    merge_crud,
    normalize_ops,
    selected_dfd_accesses,
    table_key,
    tables_without_primary_key,
    validate_stage,
)
from app.detailed_design.validation import validate_data_model
from app.uml.domain.er import ErColumn, ErElement, ErSemanticModel


def _function_list() -> FunctionListModel:
    return FunctionListModel.model_validate(
        {
            "groups": ["予約"],
            "functions": [
                {"id": "F-01", "name": "予約を登録する", "group": "予約"},
                {"id": "F-02", "name": "予約を一覧する", "group": "予約"},
                {"id": "F-03", "name": "ログインする", "group": "予約"},
            ],
            "next_number": 4,
        }
    )


def _dfd() -> dict:
    """利用者 → F-01 → reservations(書き込み)、Reservations → F-02(読み。名前の大小は無視)。"""
    return {
        "elements": [
            {"id": "user", "name": "利用者", "element_type": "external_entity"},
            {"id": "F-01", "name": "F-01 予約を登録する", "element_type": "process"},
            {"id": "F-02", "name": "F-02 予約を一覧する", "element_type": "process"},
            {"id": "s1", "name": "reservations", "element_type": "data_store"},
            {"id": "s2", "name": " Reservations ", "element_type": "data_store"},
        ],
        "relations": [
            {"id": "r1", "source_id": "user", "target_id": "F-01"},
            {"id": "r2", "source_id": "F-01", "target_id": "s1"},
            {"id": "r3", "source_id": "s2", "target_id": "F-02"},
            {"id": "r4", "source_id": "F-02", "target_id": "user"},
            {"id": "r5", "source_id": "F-01", "target_id": "missing"},
        ],
    }


def test_merge_then_validate_without_errors() -> None:
    """統合スモーク: DFD の R/W と AI の下書きから組み立てた CRUD 図が、検証を通る。"""
    accesses = dfd_accesses([_dfd()])
    drafts = [CrudDraft("F-01", "reservations", "c"), CrudDraft("F-03", "users", "R")]
    model = merge_crud(drafts, accesses, _function_list(), ["reservations", "users"])
    sources = StageSources(
        stages={1: _function_list().model_dump(), 2: {"dfd_groups": ["予約"], "summaries": []}},
        dfd_diagrams={
            dfd_subject("予約"): DfdDiagramSummary(
                "approved", "completed", accesses=tuple(accesses)
            )
        },
        er_diagram=ErDiagramSummary("approved", "completed", tables=("reservations", "users")),
    )
    issues = validate_stage(DATA_MODEL_STAGE, model.model_dump(), sources)
    assert not has_errors(issues)
    assert [i.code for i in issues] == ["DRAFT_CELLS"]
    assert STAGE_VALIDATORS[3] is validate_data_model
    assert ER_SUBJECT == ""
    assert CRUD_OPS == "CRUD"


def test_dfd_accesses_reads_store_process_lines_only() -> None:
    assert dfd_accesses([_dfd()]) == [
        DfdAccess("F-01", "reservations", "write"),
        DfdAccess("F-02", "reservations", "read"),
    ]
    assert dfd_accesses([]) == []


def test_merge_crud_marks_drafts_and_keeps_dfd_reads_fixed() -> None:
    accesses = [
        DfdAccess("F-01", "reservations", "write"),
        DfdAccess("F-02", "reservations", "read"),
    ]
    drafts = [
        CrudDraft("F-01", "Reservations", "rc"),  # 名前の大小と操作の並びは直す
        CrudDraft("F-03", "users", "RX"),  # C/R/U/D 以外は捨てる
        CrudDraft("F-09", "users", "R"),  # 機能一覧に無い処理
        CrudDraft("F-01", "unknown", "C"),  # ER に無いテーブル
    ]
    model = merge_crud(drafts, accesses, _function_list(), ["reservations", "users"])
    assert [(c.function_id, c.table, c.ops, c.draft) for c in model.cells] == [
        ("F-01", "reservations", "CR", True),
        ("F-02", "reservations", "R", False),  # DFD の読みだけ = 確定
        ("F-03", "users", "R", True),  # DFD に無い処理の分 = 下書き
    ]


def test_merge_crud_keeps_dfd_write_without_cud_as_empty_draft() -> None:
    accesses = [DfdAccess("F-01", "reservations", "write")]
    model = merge_crud([], accesses, _function_list(), ["reservations"])
    assert [(c.ops, c.draft) for c in model.cells] == [("", True)]


def test_confirm_drafts_clears_all_marks() -> None:
    confirmed = confirm_drafts(crud_model(draft=True))
    assert CrudModel.model_validate(confirmed).cells[0].draft is False


def test_helpers() -> None:
    assert normalize_ops("dRc") == "CRD"
    assert table_key(" Users ") == "users"
    er = er_model(tables=("users", "projects"))
    er["elements"][1]["columns"][0]["is_primary_key"] = False
    assert er_table_names(er) == ["users", "projects"]
    assert tables_without_primary_key(er) == ["projects"]
    assert er_table_names(None) == []


def test_er_column_and_table_notes_default_to_empty() -> None:
    """ER の列・テーブルの制約・説明は任意(既存の ER・簡易ドキュメントモードはそのまま読める)。"""
    column = ErColumn(name="email", type="VARCHAR(255)")
    assert (column.constraints, column.description) == ("", "")
    table = ErElement(id="t", name="users", columns=[column])
    assert table.description == ""
    model = ErSemanticModel.model_validate(
        {
            "elements": [
                {
                    "id": "t",
                    "name": "users",
                    "description": "利用者",
                    "columns": [{"name": "email", "type": "TEXT", "constraints": "UNIQUE"}],
                }
            ]
        }
    )
    assert model.elements[0].columns[0].constraints == "UNIQUE"


def _sources(
    *, er: ErDiagramSummary | None, accesses: tuple[DfdAccess, ...] = (), groups=("reservations",)
) -> StageSources:
    return StageSources(
        stages={1: function_list_model(), 2: data_flow_model(dfd_groups=list(groups))},
        dfd_diagrams={
            dfd_subject("reservations"): DfdDiagramSummary(
                "approved", "completed", ("F-01",), accesses
            )
        },
        er_diagram=er,
    )


def _approved_er(*tables: str, without_pk: tuple[str, ...] = ()) -> ErDiagramSummary:
    return ErDiagramSummary("approved", "completed", tables or ("reservations",), without_pk)


def _codes(model: dict, sources: StageSources) -> list[str]:
    return [issue.code for issue in validate_data_model(model, sources)]


def test_validate_requires_approved_er() -> None:
    assert _codes(crud_model(), _sources(er=None)) == ["ER_MISSING"]
    generating = ErDiagramSummary("draft", "generating", ("reservations",))
    assert _codes(crud_model(), _sources(er=generating)) == ["ER_GENERATING"]
    draft = ErDiagramSummary("draft", "completed", ("reservations",))
    assert _codes(crud_model(), _sources(er=draft)) == ["ER_NOT_APPROVED"]
    assert _codes(crud_model(), _sources(er=_approved_er())) == []


def test_validate_rejects_er_without_tables() -> None:
    """段階2で DFD を描かないと ER の下書きが空になる。テーブルの無い ER では承認できない。"""
    empty = ErDiagramSummary("approved", "completed", ())
    sources = StageSources(stages={1: function_list_model()}, er_diagram=empty)

    issues = validate_data_model({"cells": []}, sources)

    assert [i.code for i in issues if i.severity == "error"] == ["ER_EMPTY"]


def test_validate_rejects_duplicate_table_names() -> None:
    """テーブル名は CRUD 図のセルを引く鍵なので、ER で名前が重なると承認できない。"""
    er = _approved_er("reservations", "new_table", " New_Table")
    issues = validate_data_model(crud_model(), _sources(er=er))
    assert [(i.code, i.target) for i in issues if i.severity == "error"] == [
        ("DUPLICATE_TABLE", " New_Table")
    ]


def test_validate_cells() -> None:
    model = {
        "cells": [
            {"function_id": "F-09", "table": "reservations", "ops": "C"},
            {"function_id": "F-01", "table": "unknown", "ops": "R"},
            {"function_id": "F-01", "table": "reservations", "ops": "RC"},
            {"function_id": "F-01", "table": "Reservations", "ops": "C"},
            {"function_id": "F-01", "table": "users", "ops": ""},
        ]
    }
    codes = _codes(model, _sources(er=_approved_er("reservations", "users")))
    assert codes == [
        "UNKNOWN_FUNCTION",
        "UNKNOWN_TABLE",
        "INVALID_OPS",
        "DUPLICATE_CELL",
        "EMPTY_OPS",
    ]
    assert _codes({"cells": "x"}, _sources(er=None)) == ["INVALID_MODEL"]


def test_validate_against_dfd_lines() -> None:
    read = DfdAccess("F-01", "reservations", "read")
    write = DfdAccess("F-01", "reservations", "write")
    sources = _sources(er=_approved_er(), accesses=(read, write))
    assert _codes(crud_model(ops="C"), sources) == ["DFD_READ_MISSING"]
    assert _codes(crud_model(ops="R"), sources) == ["DFD_WRITE_MISSING"]
    # 空のセルは EMPTY_OPS を重ねない(DFD の書き込みの指摘で足りる)
    assert _codes(crud_model(ops=""), sources) == ["DFD_READ_MISSING", "DFD_WRITE_MISSING"]
    assert _codes(crud_model(ops="CR"), sources) == []
    # DFD を描くと選んでいないグループの DFD は見ない
    unselected = _sources(er=_approved_er(), accesses=(read,), groups=())
    assert _codes(crud_model(ops="C"), unselected) == []


def test_validate_warnings() -> None:
    stray = DfdAccess("F-01", "logs", "write")
    sources = _sources(
        er=_approved_er("reservations", "users", without_pk=("users",)), accesses=(stray,)
    )
    issues = validate_data_model(crud_model(draft=True), sources)
    assert not has_errors(issues)
    assert [(i.code, i.target) for i in issues] == [
        ("DRAFT_CELLS", None),
        ("STORE_NOT_IN_ER", "logs"),
        ("UNUSED_TABLE", "users"),
        ("TABLE_WITHOUT_PK", "users"),
    ]
    assert selected_dfd_accesses(sources) == [stray]
