"""内部設計書(Markdown)に、承認済みのUML図の範囲をアンカーコメントで区切って差し込む純粋関数群。

アンカーの形式(docs/internal_design.md 3.3節「UML設計図パイプラインの図↔文書対応」):

    <!-- uml:diagram:<diagram_id>:start v=<version> -->
    (要素表などの本文)
    <!-- uml:diagram:<diagram_id>:end -->

- アンカーは図のIDを含むため、文書を生成するLLMには書けない。反映のときに、見出しを手がかりに
  バックエンドが決定的に挿入する(プロンプトは改訂しない)。
- `v=<version>`は、反映したときの図の`version`。文書の中に持たせることで、文書を復元すると
  古いアンカー(古い版)も一緒に戻り、陳腐化の判定(staleness.py)が文書の内容だけで完結する。
- 同じ図のアンカーが既にあれば、その位置のまま本文を置き換える(何度反映しても1か所)。
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.uml.domain import NotationType
from app.uml.generation.sections import find_dfd_heading_end, find_section_heading_end

# 挿入先の見出しが見つからない図を集める節(文書の末尾に作る)
APPENDIX_HEADING = "## 付録: 設計図"

_START = re.compile(r"<!-- uml:diagram:(?P<id>[0-9A-Za-z-]+):start v=(?P<version>\d+) -->")
_END_TEMPLATE = "<!-- uml:diagram:{diagram_id}:end -->"

# 記法ごとの挿入先の節(DFDは節ではなく、対象の`#### DF-n: <処理名>`見出しの直下)
_SECTION_FOR_NOTATION: dict[NotationType, str] = {"component": "3.3", "er": "3.2"}


@dataclass(frozen=True)
class AnchorBlock:
    """文書の中の、図1枚分のアンカーの範囲。`start`〜`end`は開始コメントの先頭から
    終了コメントの末尾まで(`markdown[start:end]`がブロック全体)。`body`は2つのコメントの間。"""

    diagram_id: str
    version: int
    start: int
    end: int
    body: str


def render_block(diagram_id: str, version: int, body: str) -> str:
    """アンカーで囲んだブロックの文字列を作る。本文の前後に空行を入れる ── 表の直後に
    終了コメントが続くと、処理系によっては表の続きと解釈されるため。"""
    return (
        f"<!-- uml:diagram:{diagram_id}:start v={version} -->\n\n"
        f"{body.strip()}\n\n"
        f"{_END_TEMPLATE.format(diagram_id=diagram_id)}"
    )


def parse_anchors(markdown: str) -> list[AnchorBlock]:
    """文書の中のアンカーを出現順に列挙する。終了コメントが無い開始コメントは無視する
    (手で壊れた範囲を推測で直さない。次の反映では、壊れた開始コメントを残したまま新しく挿入する)。"""
    blocks: list[AnchorBlock] = []
    position = 0
    while (match := _START.search(markdown, position)) is not None:
        diagram_id = match.group("id")
        end_marker = _END_TEMPLATE.format(diagram_id=diagram_id)
        end_index = markdown.find(end_marker, match.end())
        if end_index == -1:
            position = match.end()
            continue
        blocks.append(
            AnchorBlock(
                diagram_id=diagram_id,
                version=int(match.group("version")),
                start=match.start(),
                end=end_index + len(end_marker),
                body=markdown[match.end() : end_index].strip(),
            )
        )
        position = end_index + len(end_marker)
    return blocks


def find_block(markdown: str, diagram_id: str) -> AnchorBlock | None:
    """指定した図のアンカーを探す。無ければNone。"""
    return next((b for b in parse_anchors(markdown) if b.diagram_id == diagram_id), None)


def upsert_block(
    markdown: str,
    *,
    diagram_id: str,
    version: int,
    body: str,
    notation: NotationType,
    subject: str,
) -> str:
    """図1枚分のブロックを差し込んだ文書を返す(元の文字列は変えない)。

    1. 同じ図のアンカーが既にあれば、その位置のまま置き換える。
    2. 無ければ、記法ごとの見出しの直下に挿入する(component: `## 3.3`、ER: `## 3.2`、
       DFD: `#### DF-n: <subject>`)。見出しの直下に既に他の図のブロックが続いていれば、
       その後ろに並べる(反映した順に並ぶ)。
    3. 見出しが見つからなければ、末尾の`## 付録: 設計図`節へ入れる(節が無ければ作る)。
    """
    block = render_block(diagram_id, version, body)
    existing = find_block(markdown, diagram_id)
    if existing is not None:
        return markdown[: existing.start] + block + markdown[existing.end :]

    heading_end = _heading_end_for(markdown, notation, subject)
    if heading_end is None:
        return _insert_into_appendix(markdown, block)
    return _insert_after_blocks(markdown, heading_end, block)


def _heading_end_for(markdown: str, notation: NotationType, subject: str) -> int | None:
    if notation == "dfd":
        return find_dfd_heading_end(markdown, subject)
    return find_section_heading_end(markdown, _SECTION_FOR_NOTATION[notation])


def _insert_after_blocks(markdown: str, index: int, block: str) -> str:
    """`index`(見出し行の直後)から、既に続いているブロックを読み飛ばした位置に挿入する。"""
    position = index
    while True:
        rest_start = len(markdown) - len(markdown[position:].lstrip())
        following = parse_anchors(markdown[rest_start:])
        if not following or following[0].start != 0:
            break
        position = rest_start + following[0].end
    before = markdown[:position].rstrip("\n")
    after = markdown[position:].lstrip("\n")
    return f"{before}\n\n{block}\n\n{after}" if after else f"{before}\n\n{block}\n"


def _insert_into_appendix(markdown: str, block: str) -> str:
    appendix = re.search(rf"^{re.escape(APPENDIX_HEADING)}\s*$", markdown, re.MULTILINE)
    if appendix is not None:
        newline = markdown.find("\n", appendix.end())
        heading_end = len(markdown) if newline == -1 else newline + 1
        return _insert_after_blocks(markdown, heading_end, block)
    return f"{markdown.rstrip()}\n\n{APPENDIX_HEADING}\n\n{block}\n"


@dataclass(frozen=True)
class ImageLink:
    """zipの中の図ファイルへのリンク1件(`path`はmdからの相対パス)。"""

    title: str
    path: str


def with_image_links(markdown: str, links: Mapping[str, ImageLink]) -> str:
    """`links`に含まれる図のブロックの先頭(開始コメントの直後)に、画像リンクを入れた文書を返す。
    パスは空白や日本語を含みうるため、`<...>`で囲む形(CommonMarkのリンク先の書き方)にする。"""
    result = markdown
    # 後ろのブロックから書き換えると、前のブロックの位置がずれない
    for block in reversed(parse_anchors(markdown)):
        link = links.get(block.diagram_id)
        if link is None:
            continue
        image = f"![{link.title}](<{link.path}>)"
        body = f"{image}\n\n{block.body}" if block.body else image
        rebuilt = render_block(block.diagram_id, block.version, body)
        result = result[: block.start] + rebuilt + result[block.end :]
    return result
