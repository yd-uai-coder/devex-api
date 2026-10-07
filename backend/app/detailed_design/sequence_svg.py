"""シーケンス図のモデル(sequence.py)を SVG 文字列に書き出す(純粋関数・決定的)。

docs/internal_design.md 3.3節「4. 詳細設計モード」の「シーケンス図」。

参加者 = 列、イベント(矢印・分岐の注記)= 行の単純な配置で、レイアウトエンジンを使わない。
書式(文字の幅の見積もり・フォント・矢じり)は図の出力エンジン(app/uml/export/svg.py)にそろえる。

- 同期の呼び出し = 実線と塗りの矢じり、非同期の呼び出し = 実線と開いた矢じり、戻り = 破線。
  表に無く推測した戻りは、ラベルを斜体・薄い色にする。
- 矢印のラベルの先頭に手順番号を付け、手順の表の行と対応させる。
- 列の間隔は、その間をまたぐ矢印のラベルが収まる幅に広げる。長いラベルは切り詰める
  (全文は手順の表にある)。
- 文字はすべて`html.escape`で書く(画面と HTML の詳細設計書は、この SVG をそのまま埋め込む)。
"""

import html

from app.detailed_design.sequence import SequenceDiagram, SequenceMessage, SequenceNote
from app.uml.export.svg import FONT_FAMILY
from app.uml.layout.text import FONT, tw

MARGIN = 20
HEAD_H = 34  # 参加者の箱の高さ
HEAD_PAD = 12  # 参加者の箱の左右の余白
MIN_GAP = 150  # 隣の列との最小の間隔
ROW_H = 38  # イベント1行の高さ
LABEL_PAD = 24  # 矢印のラベルの左右の余白の合計
SELF_W = 36  # 同じ参加者への呼び出しの輪の幅
MAX_LABEL = 48  # ラベルの最大の文字数(超えたら切り詰める)
SMALL = FONT - 2  # 矢印のラベルの文字サイズ


def _clip(text: str) -> str:
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "…"


def message_label(event: SequenceMessage) -> str:
    """矢印のラベル(`2: create(予約)`、推測した戻りは`(2 の戻り) 予約`)。"""
    number = event.step_id.split("#", 1)[1]
    head = f"({number} の戻り)" if event.derived else f"{number}:"
    return _clip(f"{head} {event.label}".rstrip())


def _note_text(event: SequenceNote) -> str:
    return _clip(f"{event.step_id.split('#', 1)[1]} {event.text}")


def _columns(diagram: SequenceDiagram) -> tuple[list[float], list[float], float]:
    """各列の中心の x・参加者の箱の幅・全体の幅。"""
    widths = [max(tw(p.name), 60) + 2 * HEAD_PAD for p in diagram.participants]
    index = {p.id: i for i, p in enumerate(diagram.participants)}
    count = len(widths)
    gaps = [max(MIN_GAP, (widths[i] + widths[i + 1]) / 2 + 20) for i in range(count - 1)]
    tail = widths[-1] / 2 if widths else 0.0
    for event in diagram.events:
        if not isinstance(event, SequenceMessage):
            continue
        need = tw(message_label(event), SMALL) + LABEL_PAD
        a, b = sorted((index[event.source], index[event.target]))
        if a == b:
            # 同じ参加者への呼び出しは列の右に描くので、右の間隔(最後の列なら右の余白)を広げる
            if a < count - 1:
                gaps[a] = max(gaps[a], need + SELF_W)
            else:
                tail = max(tail, need + SELF_W)
            continue
        have = sum(gaps[a:b])
        if have < need:
            extra = (need - have) / (b - a)
            for k in range(a, b):
                gaps[k] += extra
    centers: list[float] = []
    x = MARGIN + (widths[0] / 2 if widths else 0)
    for i in range(count):
        centers.append(x)
        if i < count - 1:
            x += gaps[i]
    width = (centers[-1] + tail + MARGIN) if centers else 2 * MARGIN
    return centers, widths, width


def to_sequence_svg(diagram: SequenceDiagram) -> str:
    """SVG 文字列を返す(先頭に`<?xml ...?>`は付けない)。参加者が無ければ空の図。"""
    centers, widths, width = _columns(diagram)
    index = {p.id: i for i, p in enumerate(diagram.participants)}
    top = MARGIN + HEAD_H
    height = top + ROW_H * (len(diagram.events) + 1) + MARGIN
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family=\'{FONT_FAMILY}\' font-size="{FONT}">',
        "<defs>"
        '<marker id="sq-call" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        'markerHeight="8" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#444"/></marker>'
        '<marker id="sq-open" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        'markerHeight="8" orient="auto"><path d="M0,0 L10,5 L0,10" fill="none" stroke="#444" '
        'stroke-width="1.5"/></marker>'
        "</defs>",
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="#ffffff"/>',
    ]
    for p, x, w in zip(diagram.participants, centers, widths, strict=True):
        out.append(
            f'<line x1="{x:.1f}" y1="{top:.1f}" x2="{x:.1f}" y2="{height - MARGIN:.1f}" '
            'stroke="#999" stroke-width="1" stroke-dasharray="4,4"/>'
        )
        out.append(
            f'<rect x="{x - w / 2:.1f}" y="{MARGIN}" width="{w:.1f}" height="{HEAD_H}" rx="4" '
            'fill="#eef3fb" stroke="#4a6fa5" stroke-width="1.5"/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{MARGIN + HEAD_H / 2 + 5:.1f}" text-anchor="middle" '
            f'fill="#1a1a1a">{html.escape(p.name)}</text>'
        )
    for row, event in enumerate(diagram.events, start=1):
        y = top + ROW_H * row
        if isinstance(event, SequenceNote):
            out.append(_note_svg(event, [centers[index[i]] for i in event.over], y, width))
        else:
            x1, x2 = centers[index[event.source]], centers[index[event.target]]
            out.append(_message_svg(event, x1, x2, y))
    out.append("</svg>")
    return "\n".join(out)


def _message_svg(event: SequenceMessage, x1: float, x2: float, y: float) -> str:
    dash = ' stroke-dasharray="6,4"' if event.kind == "return" else ""
    marker = "sq-open" if event.kind in ("async", "return") else "sq-call"
    style = ' font-style="italic" fill="#777"' if event.derived else ' fill="#222"'
    label = html.escape(message_label(event))
    if x1 == x2:
        # 同じ参加者への呼び出し: 右へ出て、少し下で戻る輪
        loop = f"M{x1:.1f},{y - 8:.1f} h{SELF_W} v14 h{-SELF_W + 2}"
        return (
            f'<path d="{loop}" fill="none" stroke="#444" stroke-width="1.4"{dash} '
            f'marker-end="url(#{marker})"/>'
            f'<text x="{x1 + SELF_W + 6:.1f}" y="{y - 2:.1f}" font-size="{SMALL}"{style}>'
            f"{label}</text>"
        )
    end = x2 - 2 if x2 > x1 else x2 + 2
    return (
        f'<line x1="{x1:.1f}" y1="{y:.1f}" x2="{end:.1f}" y2="{y:.1f}" stroke="#444" '
        f'stroke-width="1.4"{dash} marker-end="url(#{marker})"/>'
        f'<text x="{(x1 + x2) / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" '
        f'font-size="{SMALL}"{style}>{label}</text>'
    )


def _note_svg(event: SequenceNote, xs: list[float], y: float, width: float) -> str:
    text = _note_text(event)
    w = tw(text, SMALL) + 16
    center = (min(xs) + max(xs)) / 2 if xs else width / 2
    left = min(max(center - w / 2, 4), max(width - w - 4, 4))
    return (
        f'<rect x="{left:.1f}" y="{y - 14:.1f}" width="{w:.1f}" height="22" rx="2" '
        'fill="#fff8dc" stroke="#c9a227" stroke-width="1"/>'
        f'<text x="{left + 8:.1f}" y="{y + 2:.1f}" font-size="{SMALL}" fill="#5a4a00">'
        f"{html.escape(text)}</text>"
    )
