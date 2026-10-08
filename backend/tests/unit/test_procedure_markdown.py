"""実装手順書の Markdown(index・単位の md・AI 向けの版)の組み立て(純粋関数)のテスト。

SUT は`app/detailed_design/procedure_output/markdown.py`の`to_index_markdown`・`to_unit_markdown`・
`to_ai_markdown`・`ai_warnings`、ドライバはこのテスト。
スタブ不要 ── 対象は`ProcedureOutputSource`(フィクスチャ)だけから決まる純粋関数のため。
"""

import pytest
from tests.fixtures.detailed_design import document_stage_models, sample_procedure_source

from app.detailed_design import ProcedureModel
from app.detailed_design.document.markdown import procedure_table
from app.detailed_design.procedure_doc_refs import design_book
from app.detailed_design.procedure_output import (
    ai_warnings,
    to_ai_markdown,
    to_index_markdown,
    to_unit_markdown,
)
from app.detailed_design.procedure_output.markdown import (
    COMPLETION_CRITERIA,
    MODULE_RULE,
    PROCEDURE_UNAPPROVED_TEXT,
)
from app.detailed_design.validation import StageIssue


def _sections(markdown: str) -> list[str]:
    return [line for line in markdown.splitlines() if line.startswith("## ")]


def test_index_has_five_sections() -> None:
    """統合スモーク: index は 実装概要 → 実装前提 → 単位の一覧 → 未定義の一覧 → 完了条件。"""
    markdown = to_index_markdown(sample_procedure_source())

    assert markdown.startswith("# 実装手順書: 予約システム\n")
    assert _sections(markdown) == [
        "## 1. 実装概要",
        "## 2. 実装前提・制約",
        "## 3. 単位の一覧(依存順)",
        "## 4. 未定義・要決定の一覧(実装可能性チェックの結果)",
        "## 5. 完了条件",
    ]
    assert "| M-01 | 予約の登録 | Must | 予約を登録できる |" in markdown
    assert "- Won't have(見送り): 決済" in markdown
    assert f"- {MODULE_RULE}" in markdown
    assert "- 07章 認証: JWT で利用者を確かめる" in markdown


def test_index_unit_list_links_documented_units_only() -> None:
    markdown = to_index_markdown(sample_procedure_source())

    assert "| M-01-T01 開発環境を用意する | 基盤 | — | — | (未生成) | — | — |" in markdown
    assert (
        "| [M-01-T02 予約を登録する](./M-01-T02_予約を登録する.md) | 機能 | F-01 | M-01-T01 | "
        "`app/api/routes/reservations.py`、`tests/test_reservations.py` | テストが通る | 最重要1 |"
    ) in markdown


def test_index_lists_global_and_unit_findings() -> None:
    issue = StageIssue(
        severity="warning", code="X", message="テーブルが無い", target="段階3", level="major",
        fix_stage=3,
    )
    markdown = to_index_markdown(sample_procedure_source(issues=(issue,)))

    assert "| 中程度 | 検証 | 段階3 | テーブルが無い | 段階3 |" in markdown
    assert "| 最重要 | AI | M-01-T02 | 07章 例外と HTTP | 重複したときの応答が無い | 段階7 |" in (
        markdown
    )


def test_index_of_unapproved_stage_only_says_so() -> None:
    """段階8が承認済みでなければ(古いときも)、「未承認」とだけ書く。"""
    for state in ("reviewing", "outdated"):
        markdown = to_index_markdown(sample_procedure_source(state=state))

        assert markdown == f"# 実装手順書: 予約システム\n\n{PROCEDURE_UNAPPROVED_TEXT}\n"


def test_unit_markdown_refers_by_id_and_adds_sequence() -> None:
    """人向けの単位の md は、参照を ID(見出し)だけで書き、手順のシーケンス図は添える。"""
    markdown = to_unit_markdown(sample_procedure_source(), "M-01-T02")

    assert markdown.startswith(
        "# M-01-T02 予約を登録する\n\n"
        "> 種別: 機能 / マイルストーン: M-01 予約の登録 / 依存: M-01-T01\n"
    )
    assert "- 段階5 F-01 予約を登録する\n" in markdown
    assert "### シーケンス図(段階5 F-01 予約を登録する の手順から導出)" in markdown
    assert "```mermaid\nsequenceDiagram" in markdown
    assert "| 1 |" not in markdown  # 手順の表は書き写さない
    assert "| TC-01 | 予約を登録できる | create_reservation | API を呼ぶテスト | スタブ不要 |" in (
        markdown
    )
    assert "| 最重要 | AI | 07章 例外と HTTP | 重複したときの応答が無い | 段階7 |" in markdown


def test_ai_markdown_expands_design_in_guideline_order() -> None:
    """AI 向けの版は作成方針17章の順で、参照する設計を展開して添える。"""
    source = sample_procedure_source()
    markdown = to_ai_markdown(source, "M-01-T02")

    assert _sections(markdown) == [
        "## プロジェクト概要",
        "## 実装ルール",
        "## 今回の実装単位",
        "## 作成・変更するファイル(依存順)",
        "## 参照する設計",
        "## テスト観点",
        "## 完了条件",
        "## 未定義・要決定(決まるまで、推測で実装しないこと)",
        "## 制約",
    ]
    procedure = source.contexts["M-01-T02"].refs[0]
    assert procedure.markdown is not None and procedure.markdown in markdown
    stages = document_stage_models()
    table = procedure_table(
        ProcedureModel.model_validate(stages[5]).procedures[0], design_book(stages).logic_ids
    )
    assert "\n".join(table) in markdown  # 05 と同じ表で展開する
    assert "### 07章 横断事項" in markdown
    assert "前提: M-01-T01 まで実装済み" in markdown
    assert f"- {COMPLETION_CRITERIA[0]}" in markdown
    assert "- 確認方法: テストが通る" in markdown
    assert "- [最重要] 07章 例外と HTTP: 重複したときの応答が無い" in markdown
    assert markdown.rstrip().endswith("問題点を報告してください。")


def test_ai_warnings() -> None:
    """承認済みで未定義が無ければ警告なし。未承認・古い・未定義の残りは先頭で警告する。"""
    approved = sample_procedure_source()
    findings = list(approved.findings)

    assert ai_warnings(approved, []) == []
    assert ai_warnings(approved, findings) == [
        "> ⚠ この単位には「未定義・要決定」が 1 件残っています(最重要 1 件)。"
        "決めてから渡すことを勧めます。",
        "",
    ]
    assert "未承認" in ai_warnings(sample_procedure_source(state="reviewing"), [])[0]
    assert "古くなっています" in ai_warnings(sample_procedure_source(state="outdated"), [])[0]
    markdown = to_ai_markdown(sample_procedure_source(state="reviewing"), "M-01-T02")
    assert markdown.startswith("> ⚠ 段階8(実装手順書)は未承認です。")


def test_unit_without_procedure_raises() -> None:
    source = sample_procedure_source()

    with pytest.raises(KeyError):
        to_unit_markdown(source, "M-01-T01")
    with pytest.raises(KeyError):
        to_ai_markdown(source, "M-09-T01")
