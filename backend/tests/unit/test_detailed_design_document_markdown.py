"""詳細設計書の md の組み立てのテスト。

SUT: to_markdown / md_cell / md_table(app/detailed_design/document/markdown.py)
ドライバ: 各テスト関数
スタブ不要 ── 純粋関数(副作用なし)で、外部依存を呼ばないため。図は描画済みの値
(`RenderedDiagram`)を fixture の`sample_document_source`で渡す(描画はサービス層の責務)。
"""

from tests.fixtures.detailed_design import (
    ALL_APPROVED,
    document_stage_models,
    sample_document_source,
)

from app.detailed_design.document import to_markdown
from app.detailed_design.document.markdown import md_cell, md_table


def test_md_cell_keeps_table_shape() -> None:
    assert md_cell("a|b\nc") == "a\\|b c"
    assert md_cell("  ") == "—"
    assert md_table(["x"], [["1"]]) == ["| x |", "|---|", "| 1 |"]


def test_markdown_has_all_chapters_in_order() -> None:
    text = to_markdown(sample_document_source())

    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 01 機能(処理)一覧",
        "## 02 データフロー",
        "## 03 データモデル",
        "## 04 ソフトウェア構造",
        "## 05 主要処理の手順",
        "## 06 処理ロジックの詳細",
    ]
    assert text.startswith("# 詳細設計書: 予約システム\n")


def test_markdown_embeds_diagrams_as_relative_images() -> None:
    text = to_markdown(sample_document_source())

    assert "![データフロー図: reservations](diagrams/dfd_reservations.svg)" in text
    assert "![ER図(全体)](diagrams/er.svg)" in text
    assert "![コンポーネント図(全体)](diagrams/component.svg)" in text


def test_markdown_writes_ids_in_text_without_links_or_html() -> None:
    text = to_markdown(sample_document_source())

    assert "create_reservation → 詳細: L-01" in text
    assert "呼ばれる手順: F-01#1" in text
    assert "| 予約 | id, starts_at | F-01 |" in text  # データ辞書の使う処理
    assert "C+" in text  # DFD の書き込みの線から決まる C
    assert "<a " not in text and "<br>" not in text and "](#" not in text


def test_markdown_marks_unapproved_and_skipped_chapters() -> None:
    models = {**document_stage_models(), 6: {"logics": []}}
    text = to_markdown(sample_document_source(states={**ALL_APPROVED, 3: "draft"}, models=models))

    assert "未承認(段階3が承認されていません" in text
    assert "省略(段階6を飛ばしました)" in text
    assert "テーブル定義" not in text
    assert "→ 詳細:" not in text  # 省略なら 05 から 06 へ紐づけない
