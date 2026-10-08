"""簡易モードの、単位が参照する設計の導出と展開(純粋関数)のテスト。

SUT は`app/detailed_design/simple_procedure/refs.py`の純粋関数、ドライバはこのテスト。
スタブ不要 ── 対象は文書から読んだ設計(`SimpleDesignBook`)と単位だけから決まり、DB や LLM を
呼ばないため。
"""

from tests.fixtures.simple_procedure import (
    EXTERNAL_DESIGN_MD,
    INTERNAL_DESIGN_MD,
    LAYERED_INTERNAL_DESIGN_MD,
    PLAN_MD,
)

from app.detailed_design.procedure_doc import DesignRef, find_unit
from app.detailed_design.simple_procedure import (
    expand_simple_ref,
    parse_internal_design,
    parse_wbs,
    simple_ref_label,
    simple_unit_context,
    simple_unit_refs,
)

BOOK = parse_internal_design(INTERNAL_DESIGN_MD, EXTERNAL_DESIGN_MD)


def _unit(unit_id: str):
    unit = find_unit(parse_wbs(PLAN_MD).plan, unit_id)
    assert unit is not None
    return unit


def test_unit_context_expands_dataflow_then_modules() -> None:
    """統合スモーク: 機能の単位の参照を、DF → モジュールの順に展開し、共通の節を添える。"""
    context = simple_unit_context(_unit("M-01-T02"), BOOK, "- Docker Compose")

    assert [(r.kind, r.key, r.resolved) for r in context.refs] == [
        ("dataflow", "DF-1", True),
        ("module", "app/api/reservations.py", True),
        ("module", "app/services/reservation.py", True),
    ]
    assert [r.label for r in context.refs][:2] == [
        "内部設計書 DF-1 POST /api/v1/reservations",
        "内部設計書 3.3 `app/api/reservations.py`",
    ]
    assert all(r.svg is None and r.mermaid is None for r in context.refs)
    assert context.crosscutting.startswith("### 内部設計書 3.4 例外処理・エラー・ログ")
    assert "### 実装計画書 4.3 開発環境\n\n- Docker Compose" in context.environment
    assert "### 内部設計書 3.1 技術スタック・アーキテクチャ" in context.environment


def test_dataflow_adds_api_rows_and_tables() -> None:
    """DF の展開に、同じ API の 3.3・2.6 の行と、流れに出てくる 3.2 のテーブルを添える。"""
    ref = DesignRef("dataflow", "DF-1", True)

    markdown = expand_simple_ref(ref, BOOK)

    assert markdown is not None
    assert "| 利用者 | 予約リクエスト | 検証して保存 | reservations |" in markdown
    assert "- API(内部設計書 3.3): POST /api/v1/reservations ── 予約を登録する" in markdown
    assert "(関連画面: SCR-001)" in markdown
    assert "#### 内部設計書 3.2 テーブル: reservations" in markdown


def test_unresolved_dataflow_is_not_expanded_and_unknown_layer_is_omitted() -> None:
    """設計に無い DF は解決できない参照になり、展開しない。どの層にも当たらないファイルは
    参照に出さない。"""
    unit = _unit("M-02-T01")
    task = unit.task.model_copy(update={"function_ids": ["DF-9"], "modules": ["config/x.toml"]})

    refs = simple_unit_refs(task, BOOK)

    assert [(r.kind, r.key, r.resolved) for r in refs] == [("dataflow", "DF-9", False)]
    assert expand_simple_ref(refs[0], BOOK) is None
    assert simple_ref_label(refs[0], BOOK) == "内部設計書 DF-9"


def test_unit_without_dataflow_gets_the_data_model() -> None:
    """DF を持たない単位(基盤)には、3.2節のデータモデル全体を添える。"""
    refs = simple_unit_refs(_unit("M-01-T01").task, BOOK)

    assert [(r.kind, r.key) for r in refs] == [("datamodel", "3.2"), ("module", "app/main.py")]
    assert simple_ref_label(refs[0], BOOK) == "内部設計書 3.2 データモデル"
    markdown = expand_simple_ref(refs[0], BOOK)
    assert markdown is not None and "| id | UUID | PK | 予約ID |" in markdown


def test_module_is_expanded_with_its_layer() -> None:
    """層ごとにまとめた一覧では、ファイルの属する層の行を展開する(DF のテーブルも拾う)。"""
    book = parse_internal_design(LAYERED_INTERNAL_DESIGN_MD)
    task = _unit("M-01-T02").task.model_copy(
        update={"modules": ["backend/app/api/reservations.py", "backend/main.py"]}
    )

    context = simple_unit_context(_unit("M-01-T02"), book, "")
    refs = simple_unit_refs(task, book)

    assert [(r.kind, r.key) for r in refs] == [
        ("dataflow", "DF-1"),
        ("module", "backend/app/api/reservations.py"),
    ]
    assert expand_simple_ref(refs[1], book) == (
        "- 内部設計書 3.3 `backend/app/api/reservations.py` → 層 `app/api/*.py`"
        "(ルーター): リクエストの受け付け / 依存先: Services"
    )
    assert "#### 内部設計書 3.2 テーブル: reservations" in (context.refs[0].markdown or "")
