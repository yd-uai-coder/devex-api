"""詳細設計書の HTML の組み立てのテスト。

SUT: to_html / badge / figure(app/detailed_design/document/html.py)
ドライバ: 各テスト関数
スタブ不要 ── 純粋関数(副作用なし)で、外部依存を呼ばないため。
"""

import re
from html.parser import HTMLParser

from tests.fixtures.detailed_design import (
    ALL_APPROVED,
    document_stage_models,
    sample_document_source,
)

from app.detailed_design.document import RenderedDiagram, to_html
from app.detailed_design.document.html import badge, figure


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        if tag == "a" and str(values.get("href", "")).startswith("#"):
            self.hrefs.append(str(values["href"])[1:])


def test_html_is_self_contained() -> None:
    html = to_html(sample_document_source())

    assert html.startswith("<!doctype html>")
    assert "<style>" in html and "<script>" in html
    assert not re.search(r'<(script|link)[^>]+(src|href)="http', html)


def test_every_internal_link_has_a_target() -> None:
    collector = _Collector()
    collector.feed(to_html(sample_document_source()))

    assert collector.hrefs  # バッジ・タブ・目次がある
    assert set(collector.hrefs) <= collector.ids


def test_html_links_05_and_06_both_ways() -> None:
    html = to_html(sample_document_source())

    assert 'id="f-01-1"' in html  # 手順の行
    assert 'href="#l-01">詳細 L-01 ↓</a>' in html  # 05 → 06
    assert 'href="#f-01-1">↑ F-01#1</a>' in html  # 06 → 05
    assert '<tr id="f-01-1a" class="branch">' in html
    assert "<th>種別</th>" in html and "<td>同期</td>" in html
    assert 'aria-label="F-01 のシーケンス図"><svg ' in html  # 05 の処理のシーケンス図


def test_html_escapes_text_but_embeds_svg_as_is() -> None:
    models = document_stage_models()
    models[1]["functions"][0]["name"] = "<b>予約</b>"
    diagram = RenderedDiagram(title="図", path="diagrams/x.svg", svg='<svg id="raw"></svg>')

    html = to_html(sample_document_source(models=models))

    assert "&lt;b&gt;予約&lt;/b&gt;" in html
    assert "<b>予約</b>" not in html
    assert '<svg id="raw"></svg>' in figure(diagram)
    assert badge("F-01#1") == '<a class="badge" href="#f-01-1">F-01#1</a>'


def test_html_marks_unapproved_and_skipped_chapters() -> None:
    models = {**document_stage_models(), 6: {"logics": []}}

    html = to_html(sample_document_source(states={**ALL_APPROVED, 2: "outdated"}, models=models))

    assert "未承認 ── 段階2が承認されていません" in html
    assert "省略 ── 段階6を飛ばしました。" in html
    assert 'data-group="dfd"' not in html
