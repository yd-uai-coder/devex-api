"""段階8 単位が参照する設計の展開(純粋関数)のテスト。

SUT は`app/detailed_design/procedure_doc_refs.py`の純粋関数(と`procedure_doc.find_unit`)、
ドライバはこのテスト。
スタブ不要 ── 対象は入力の段階の内容(辞書)だけから決まり、DB や LLM を呼ばないため。
"""

from tests.fixtures.detailed_design import document_stage_models

from app.detailed_design import PlanModel, ProcedureModel, find_unit
from app.detailed_design.document.markdown import logic_spec, procedure_table
from app.detailed_design.logic import LogicModel
from app.detailed_design.procedure_doc_refs import (
    crosscutting_section,
    design_book,
    environment_section,
    unit_context,
)


def _unit(stages: dict[int, dict], unit_id: str = "M-01-T02"):
    unit = find_unit(PlanModel.model_validate(stages[7]), unit_id)
    assert unit is not None
    return unit


def test_unit_context_expands_refs_in_order() -> None:
    """統合スモーク: 機能の単位の参照を、手順 → 関数 → モジュールの順に展開する。"""
    stages = document_stage_models()

    context = unit_context(_unit(stages), stages)

    assert [(r.kind, r.key, r.resolved, r.via) for r in context.refs] == [
        ("procedure", "F-01", True, None),
        ("logic", "app/api/routes/reservations.py::create_reservation", True, "F-01#1"),
        ("module", "app/api/routes/reservations.py", True, None),
    ]
    assert [r.label for r in context.refs] == [
        "段階5 F-01 予約を登録する",
        "段階6 L-01 create_reservation(`app/api/routes/reservations.py`)",
        "段階4 `app/api/routes/reservations.py`",
    ]


def test_procedure_and_logic_use_document_tables() -> None:
    """手順と関数は、詳細設計書の 05・06 と同じ表で展開する(L-ID のリンクも同じ)。"""
    stages = document_stage_models()
    procedure = ProcedureModel.model_validate(stages[5]).procedures[0]
    logic = LogicModel.model_validate(stages[6]).logics[0]

    procedure_md, logic_md, module_md = (
        r.markdown for r in unit_context(_unit(stages), stages).refs
    )

    assert procedure_md is not None and logic_md is not None
    assert procedure_md.startswith(
        "### 段階5 F-01 予約を登録する\n\nトリガー: POST /api/v1/reservations"
    )
    ids = design_book(stages).logic_ids
    assert "\n".join(procedure_table(procedure, ids)) in procedure_md
    assert "→ 詳細: L-01" in procedure_md
    assert logic_md.startswith("### 段階6 L-01 create_reservation")
    assert "呼ばれる手順: F-01#1" in logic_md
    assert logic_md.endswith("\n".join(logic_spec(logic)))
    assert module_md == "- 段階4 `app/api/routes/reservations.py`(api): 予約の API / 依存先: なし"


def test_unresolved_refs_are_not_expanded() -> None:
    """段階5・6に無い参照は展開しない(見出しは出す)。手順が無いので関数の参照も導かれない。"""
    stages = document_stage_models()
    stages[5] = {"procedures": []}
    stages[6] = {"logics": []}

    refs = unit_context(_unit(stages), stages).refs

    assert [(r.kind, r.resolved, r.markdown) for r in refs[:1]] == [("procedure", False, None)]
    assert refs[0].label == "段階5 F-01 予約を登録する"
    assert [r.kind for r in refs] == ["procedure", "module"]


def test_base_unit_has_no_refs_but_common_sections() -> None:
    """基盤の単位は参照を持たず、段階7の 07章と開発環境だけを添える。"""
    stages = document_stage_models()

    context = unit_context(_unit(stages, "M-01-T01"), stages)

    assert context.refs == ()
    assert context.crosscutting.startswith("### 07章 横断事項\n\n| 項目 | 方針 |")
    assert "| 例外と HTTP | ドメイン例外を共通の形に変換する |" in context.crosscutting
    assert context.environment == "### 段階7 開発環境\n\nPython 3.13 と PostgreSQL"


def test_empty_plan_has_empty_sections() -> None:
    plan = PlanModel()

    assert (crosscutting_section(plan), environment_section(plan)) == ("", "")


def test_find_unit() -> None:
    plan = PlanModel.model_validate(document_stage_models()[7])

    found = find_unit(plan, " M-01-T02 ")

    assert found is not None and found.task.title == "予約を登録する"
    assert find_unit(plan, "M-09-T01") is None
