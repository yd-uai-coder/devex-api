"""実装手順書の HTML(1枚)の組み立て(純粋関数)のテスト。

SUT は`app/detailed_design/procedure_output/html.py`の`to_procedure_html`(と、共有する
`document/html.py`の`page`)、ドライバはこのテスト。
スタブ不要 ── 対象は`ProcedureOutputSource`(フィクスチャ)だけから決まる純粋関数のため。
"""

from tests.fixtures.detailed_design import procedure_doc_model, sample_procedure_source
from tests.fixtures.simple_procedure import sample_simple_procedure_source

from app.detailed_design.document.html import page
from app.detailed_design.procedure_output import to_procedure_html
from app.detailed_design.procedure_output.html import PROCEDURE_UNAPPROVED_HTML


def test_html_is_self_contained_and_deterministic() -> None:
    """統合スモーク: 自己完結の1ページで、同じ入力なら同じ出力。"""
    html = to_procedure_html(sample_procedure_source())

    assert html == to_procedure_html(sample_procedure_source())
    assert html.startswith("<!doctype html>")
    assert "<title>実装手順書: 予約システム</title>" in html
    assert "<script src" not in html and "<link" not in html
    for number in range(1, 7):
        assert f'id="proc{number}"' in html


def test_units_are_listed_and_linked_by_anchor() -> None:
    """手順書のある単位は、一覧・依存・未定義のバッジから単位の節(アンカー)へ移れる。"""
    html = to_procedure_html(sample_procedure_source())

    assert '<article id="m-01-t02"' in html
    assert '<a class="badge" href="#m-01-t02">M-01-T02</a> 予約を登録する' in html
    # 手順書の無い単位はバッジにしない(移る先が無い)
    assert '<span class="mono">M-01-T01</span> 開発環境を用意する' in html
    assert 'href="#m-01-t01"' not in html
    assert "(未生成)" in html


def test_unit_section_embeds_sequence_svg() -> None:
    html = to_procedure_html(sample_procedure_source())

    assert "シーケンス図(段階5 F-01 予約を登録する の手順から導出)" in html
    assert "<li>段階4 app/api/routes/reservations.py</li>" in html  # md の`は除く
    label = "段階5 F-01 予約を登録する のシーケンス図"
    assert f'<div class="figure" role="img" aria-label="{label}"><svg' in html


def test_text_is_escaped() -> None:
    model = procedure_doc_model()
    model["units"][0]["purpose"] = "<b>登録</b> & 確認"

    html = to_procedure_html(sample_procedure_source(model=model))

    assert "&lt;b&gt;登録&lt;/b&gt; &amp; 確認" in html
    assert "<b>登録</b>" not in html


def test_unapproved_html_only_says_so() -> None:
    html = to_procedure_html(sample_procedure_source(state="outdated"))

    assert PROCEDURE_UNAPPROVED_HTML in html
    assert 'id="proc1"' not in html
    assert "<article" not in html


def test_page_is_shared() -> None:
    assert page("t", "<p>x</p>").startswith('<!doctype html>\n<html lang="ja">')


def test_simple_mode_html_names_documents() -> None:
    """簡易モードの HTML は、元を「4文書」、直す先を文書の名前で書く。"""
    html = to_procedure_html(sample_simple_procedure_source())

    assert "承認済みの段階8と4文書から組み立てた" in html
    assert "<h3>実装ルール(内部設計書 3.3・3.4より)</h3>" in html
    assert "<td>内部設計書</td>" in html
