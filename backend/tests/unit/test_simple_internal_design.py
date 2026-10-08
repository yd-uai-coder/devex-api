"""簡易モードの内部設計書の解析(純粋関数)のテスト。

SUT は`app/detailed_design/simple_procedure/internal_design.py`の`parse_internal_design`と、
`app/detailed_design/api_list.py`の表の部品(別の節を読む`extract_api_endpoints`)。
ドライバはこのテスト。スタブ不要 ── 対象は文書の Markdown だけから決まり、DB や LLM を呼ばないため。
"""

from tests.fixtures.simple_procedure import (
    EXTERNAL_DESIGN_MD,
    INTERNAL_DESIGN_MD,
    LAYERED_INTERNAL_DESIGN_MD,
)

from app.detailed_design.api_list import extract_api_endpoints, is_separator_row, table_cells
from app.detailed_design.simple_procedure import module_layer, parse_internal_design


def test_reads_modules_dataflows_and_sections() -> None:
    """統合スモーク: モジュール一覧・DF・API・テーブル・3.1・3.4を読む。"""
    book = parse_internal_design(INTERNAL_DESIGN_MD, EXTERNAL_DESIGN_MD)

    assert book.has_module_list is True
    assert list(book.modules) == [
        "app/main.py",
        "app/api/reservations.py",
        "app/services/reservation.py",
    ]
    route = book.modules["app/api/reservations.py"]
    assert (route.layer, route.responsibility, route.depends_on) == (
        "api",
        "予約のルート",
        ["app/services/reservation.py"],
    )
    assert book.modules["app/services/reservation.py"].depends_on == []

    assert list(book.dataflows) == ["DF-1", "DF-2"]
    flow = book.dataflows["DF-1"]
    assert flow.trigger == ("POST", "/api/v1/reservations")
    assert flow.tables == ("reservations",)
    assert "- データ項目: 予約リクエスト(item_id, start_at)" in flow.markdown

    assert list(book.apis) == ["POST /api/v1/reservations", "GET /api/v1/reservations"]
    assert list(book.external_apis) == ["POST /api/v1/reservations"]
    assert "| id | UUID | PK | 予約ID |" in book.tables["reservations"]
    assert book.data_model.startswith("### テーブル: reservations")
    assert book.architecture == "- FastAPI と PostgreSQL"
    assert book.error_policy.splitlines() == [
        "- エラーは {code, message} の形で返す",
        "- ログは JSON で出す",
    ]


def test_old_internal_design_has_no_module_list() -> None:
    """モジュール一覧の無い内部設計書は`has_module_list`が False(検証が指摘する)。"""
    markdown = INTERNAL_DESIGN_MD.replace("### モジュール一覧", "### 構成")

    book = parse_internal_design(markdown)

    assert book.has_module_list is False
    assert book.modules == {}
    assert book.external_apis == {}


def test_api_list_reads_another_section() -> None:
    """API 一覧の読み取りは節を選べ、メソッドの列が無い表(モジュール一覧)は読み飛ばす。"""
    endpoints = extract_api_endpoints(INTERNAL_DESIGN_MD, "3.3")

    assert [e.key for e in endpoints] == ["POST /api/v1/reservations", "GET /api/v1/reservations"]
    assert table_cells("| a | b |") == ["a", "b"]
    assert table_cells("a | b") is None
    assert is_separator_row(["---", ":---:"]) is True


def test_dataflow_tables_are_found_in_the_text() -> None:
    """表の先が「データベース (`reservations` テーブル)」でも、本文に名前の出るテーブルを拾う。
    見出しの後ろの説明は名前に含めず、似た名前(`reservations_log`)の中では一致させない。"""
    markdown = LAYERED_INTERNAL_DESIGN_MD.replace(
        "### テーブル: reservations", "### テーブル: `reservations`(予約)"
    ).replace(
        "- データ項目: 予約リクエスト", "- 記録: reservations_log\n- データ項目: 予約リクエスト"
    )

    book = parse_internal_design(markdown)

    assert list(book.tables) == ["reservations"]
    assert book.dataflows["DF-1"].tables == ("reservations",)
    assert book.dataflows["DF-2"].tables == ("reservations",)


def test_module_layer_matches_patterns_and_directories() -> None:
    """ファイルは層まで照合する: 完全一致 → パターン(`*`・`{a,b}`。前置きは見ない)→
    同じディレクトリ。"""
    exact = parse_internal_design(INTERNAL_DESIGN_MD)
    layered = parse_internal_design(LAYERED_INTERNAL_DESIGN_MD)

    assert module_layer("app/main.py", exact) is exact.modules["app/main.py"]
    assert module_layer("app/services/other.py", exact) is exact.modules[
        "app/services/reservation.py"
    ]
    layer = module_layer("backend/app/api/reservations.py", layered)
    assert layer is not None and layer.layer == "ルーター"
    model = module_layer("backend/app/models/item.py", layered)
    assert model is not None and model.layer == "モデル"
    assert module_layer("backend/app/models/user.py", layered) is model  # 同じディレクトリ
    assert module_layer("backend/Dockerfile", layered) is None
    assert module_layer("backend/app/core/config.py", layered) is None
