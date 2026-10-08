"""段階8の土台(モードごとの作業単位・参照・共通の節)のテスト。

SUT は`app/detailed_design/procedure_basis.py`の`procedure_basis`、ドライバはこのテスト。
スタブ不要 ── 対象は段階の内容(辞書)・文書の本文だけから決まり、DB や LLM を呼ばないため。
"""

from tests.fixtures.detailed_design import document_stage_models
from tests.fixtures.simple_procedure import simple_documents

from app.detailed_design.plan import unit_ids
from app.detailed_design.procedure_basis import (
    DETAILED_LABELS,
    MODULE_RULE,
    SIMPLE_LABELS,
    SIMPLE_MODULE_RULE,
    procedure_basis,
)
from app.detailed_design.procedure_doc_refs import unit_context


def test_detailed_basis_keeps_stage7_and_unit_context() -> None:
    """統合スモーク: 詳細設計モードは段階7の単位と、今までの`unit_context`をそのまま使う。"""
    stages = document_stage_models()

    basis = procedure_basis("detailed", stages, {})

    assert basis.labels == DETAILED_LABELS
    assert [u.unit_id for u in basis.units] == ["M-01-T01", "M-01-T02"]
    unit = basis.units[1]
    assert basis.context(unit) == unit_context(unit, stages)
    assert [r.kind for r in basis.refs(unit.task)] == ["procedure", "logic", "module"]
    assert basis.rules[0] == MODULE_RULE
    assert basis.rules[1] == "07章 例外と HTTP: ドメイン例外を共通の形に変換する"
    assert basis.environment == "Python 3.13 と PostgreSQL"


def test_simple_basis_reads_wbs_and_internal_design() -> None:
    """簡易モードは WBS の単位と、内部設計書の参照・3.1・3.4・4.3を使う。"""
    basis = procedure_basis("simple", {}, simple_documents())

    assert basis.labels == SIMPLE_LABELS
    assert unit_ids(basis.plan) == ["M-01-T01", "M-01-T02", "M-02-T01"]
    assert basis.wbs_issues == ()
    unit = basis.units[1]
    assert [r.kind for r in basis.refs(unit.task)] == ["dataflow", "module", "module"]
    assert basis.context(unit).refs[0].label == "内部設計書 DF-1 POST /api/v1/reservations"
    assert basis.rules == (
        SIMPLE_MODULE_RULE,
        "3.4 エラーは {code, message} の形で返す",
        "3.4 ログは JSON で出す",
    )
    assert basis.environment.split("\n\n") == [
        "- Docker Compose で API と DB を起動する",
        "- FastAPI と PostgreSQL",
    ]


def test_simple_basis_aligns_modules_to_module_list() -> None:
    """WBS のモジュールは、段階7と同じ規則でモジュール一覧のパスにそろえる(1行だけに当たるもの)。"""
    plan = simple_documents()["implementation_plan"].replace(
        "app/services/reservation.py", "services/reservation"
    )

    basis = procedure_basis("simple", {}, simple_documents(implementation_plan=plan))

    assert basis.plan.milestones[0].tasks[1].modules == [
        "app/api/reservations.py",
        "app/services/reservation.py",
    ]
