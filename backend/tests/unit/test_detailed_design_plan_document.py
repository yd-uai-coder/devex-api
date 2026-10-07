"""07 横断事項と実装計画の組み立て(md・HTML)のテスト。

SUT: CHAPTERS・document_source の段階7(app/detailed_design/document/source.py)、
     function_plans・UNIT_HEADERS・UNIT_KIND_LABELS(app/detailed_design/document/views.py)、
     to_markdown の chapters・to_plan_markdown(app/detailed_design/document/markdown.py)、
     to_html の chapters・to_plan_html(app/detailed_design/document/html.py)、
     パッケージの re-export(app/detailed_design/document/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。段階の内容は fixture の
`sample_document_source`で渡す(DB の読み取りはサービス層の責務)。
"""

import re

from tests.fixtures.detailed_design import (
    ALL_APPROVED,
    document_stage_models,
    plan_model,
    sample_document_source,
)

from app.detailed_design import FunctionListModel, PlanModel
from app.detailed_design.document import (
    CHAPTERS,
    FunctionPlan,
    function_plans,
    to_html,
    to_markdown,
    to_plan_html,
    to_plan_markdown,
)
from app.detailed_design.document.views import UNIT_HEADERS, UNIT_KIND_LABELS

ROUTE = "app/api/routes/reservations.py"


def _unapproved_stage7():
    return sample_document_source(states={**ALL_APPROVED, 7: "reviewing"})


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_plan_documents_are_built_from_stage7():
    source = sample_document_source()
    assert source.status(7) == "approved"
    assert source.plan is not None
    assert "## 07 横断事項" in to_markdown(source)
    assert to_plan_markdown(source).startswith("# 実装計画書: 予約システム\n")
    assert to_plan_html(source).startswith("<!doctype html>")


# --- 07 横断事項 ---


def test_chapter07_lists_crosscutting_rows():
    text = to_markdown(sample_document_source())
    chapter = text.split("## 07 横断事項", 1)[1]
    assert "| 項目 | 方針 | 関わるファイル(例) |" in chapter
    assert f"| 例外と HTTP | ドメイン例外を共通の形に変換する | {ROUTE} |" in chapter
    html = to_html(sample_document_source())
    assert 'id="ch07"' in html
    assert 'aria-label="横断事項"' in html


def test_chapter07_is_unapproved_until_stage7_is_approved():
    source = _unapproved_stage7()
    assert source.plan is None
    chapter = to_markdown(source).split("## 07 横断事項", 1)[1]
    assert "未承認(段階7が承認されていません。" in chapter


def test_chapters_argument_limits_the_document():
    # 段階7の下書きの入力は 01〜06章だけ(07 は段階7自身が作る)
    chapters = [c for c in CHAPTERS if c.stage < 7]
    text = to_markdown(sample_document_source(), chapters)
    assert "## 06 処理ロジックの詳細" in text
    assert "07 横断事項" not in text
    assert 'id="ch07"' not in to_html(sample_document_source(), chapters)


# --- 処理の割り当て ---


def test_function_plans_follow_function_list_and_mark_unplanned():
    models = document_stage_models()
    function_list = FunctionListModel.model_validate(models[1])
    function_list.functions.append(function_list.functions[0].model_copy(update={"id": "F-02"}))
    plan = PlanModel.model_validate(plan_model())
    assert function_plans(plan, function_list) == [
        FunctionPlan("F-01", "予約を登録する", ("M-01-T02",)),
        FunctionPlan("F-02", "予約を登録する", ()),
    ]
    assert function_plans(plan, None) == []


# --- 実装計画の md ---


def test_plan_markdown_has_sections_in_order():
    text = to_plan_markdown(sample_document_source())
    headings = [line for line in text.splitlines() if line.startswith("#")]
    assert headings == [
        "# 実装計画書: 予約システム",
        "## 1 マイルストーン",
        "### M-01 予約の登録(Must)",
        "## 2 処理の割り当て",
        "## 3 開発環境・事前準備",
        "## 4 想定リスクと対策",
    ]
    # マイルストーンの処理は単位の処理から導く
    assert "| M-01 | 予約の登録 | Must | 予約を登録できる | F-01 |" in text
    assert "| " + " | ".join(UNIT_HEADERS) + " |" in text
    assert "| M-01-T01 | 基盤 | 開発環境を用意する | — | — | — | Dockerfile |" in text
    assert f"| M-01-T02 | 機能 | 予約を登録する | F-01 | M-01-T01 | {ROUTE} | — |" in text
    assert "| F-01 | 予約を登録する | M-01-T02 |" in text
    assert "Python 3.13 と PostgreSQL" in text
    assert "| 予約の重複 | 一意制約で防ぐ |" in text
    # 横断事項は詳細設計書の 07章に書く
    assert "例外と HTTP" not in text


def test_plan_markdown_is_unapproved_until_stage7_is_approved():
    text = to_plan_markdown(_unapproved_stage7())
    assert text == (
        "# 実装計画書: 予約システム\n\n"
        "未承認(段階7が承認されていません。承認すると、実装計画が組み立てられます)\n"
    )


# --- 実装計画の HTML ---


def test_plan_html_links_milestones_and_units_and_escapes_text():
    models = document_stage_models()
    models[7]["milestones"][0]["name"] = "<予約>"
    html = to_plan_html(sample_document_source(models=models))
    assert "&lt;予約&gt;" in html
    assert "<予約>" not in html
    assert UNIT_KIND_LABELS == {"feature": "機能", "base": "基盤"}
    assert 'aria-label="M-01 の単位"' in html
    # M-ID・単位の ID(処理の割り当てと依存)のバッジの移り先がある
    targets = set(re.findall(r'href="#([^"]+)"', html))
    anchors = set(re.findall(r'id="([^"]+)"', html))
    assert targets == {"m-01", "m-01-t01", "m-01-t02"}
    assert targets <= anchors


def test_plan_html_marks_unplanned_function():
    models = document_stage_models()
    models[7]["milestones"][0]["tasks"][1]["function_ids"] = []
    html = to_plan_html(sample_document_source(models=models))
    assert '<span class="status">未計画</span>' in html


def test_plan_html_is_unapproved_until_stage7_is_approved():
    html = to_plan_html(_unapproved_stage7())
    assert "未承認 ── 段階7が承認されていません。" in html
    assert "マイルストーン" not in html
