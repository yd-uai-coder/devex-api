"""描画用の中間表現(`RenderDiagram`)をSVG文字列に書き出す。

出自: 別プロジェクトの自作図生成エンジン`Diagram.to_svg`/`_node_svg`をコピーし、
devexで使わない要素を除いた(レーン帯・グループ枠・分岐/端子/注記ノード・破線・両端矢印)。
描く順序(背景→辺→ノード→ラベル)と図形・文字の座標の式は移植元のまま。
決定的(AI非依存)で、同じ入力からは常に同じ文字列を返す。
"""

import html

from app.uml.export.render import RenderDiagram, RenderEdge, RenderNode, path_midpoint
from app.uml.layout.finalize import label_size
from app.uml.layout.geometry import LINE_H, PADX, PADY
from app.uml.layout.text import FONT

FONT_FAMILY = (
    '"Noto Sans JP","Noto Sans CJK JP","Yu Gothic UI","Yu Gothic","Meiryo",'
    '"Hiragino Kaku Gothic ProN",sans-serif'
)
MONO_FAMILY = '"Noto Sans Mono CJK JP","Cascadia Mono",Consolas,"Noto Sans JP",monospace'


def to_svg(diagram: RenderDiagram) -> str:
    """SVG文字列を返す(先頭に`<?xml ...?>`は付けない。そのまま.svgファイルとして開ける)。"""
    width, height = diagram.width, diagram.height
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" font-family=\'{FONT_FAMILY}\' font-size="{FONT}">',
        '<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" '
        'markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#444"/>'
        "</marker></defs>",
        f'<rect width="{width:.0f}" height="{height:.0f}" fill="#ffffff"/>',
    ]
    out.extend(_edge_svg(edge) for edge in diagram.edges)
    out.extend(_node_svg(node) for node in diagram.nodes)
    # ラベルは線とノードの上(最前面)に描く
    out.extend(_label_svg(edge) for edge in diagram.edges if edge.label)
    out.append("</svg>")
    return "\n".join(out)


def _edge_svg(edge: RenderEdge) -> str:
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in edge.points)
    end = ' marker-end="url(#ar)"' if edge.arrow else ""
    return f'<path d="{d}" fill="none" stroke="#444" stroke-width="1.6"{end}/>'


def _label_svg(edge: RenderEdge) -> str:
    cx, cy = edge.label_pos or path_midpoint(edge.points)
    lw, lh = label_size(edge.label)
    parts = [
        f'<rect x="{cx - lw / 2:.1f}" y="{cy - lh / 2:.1f}" width="{lw:.1f}" height="{lh}" '
        'fill="#ffffff" fill-opacity="0.92" rx="3"/>'
    ]
    for k, line in enumerate(edge.label.split("\n")):
        parts.append(
            f'<text x="{cx:.1f}" y="{cy - lh / 2 + LINE_H * k + 15:.1f}" text-anchor="middle" '
            f'font-size="{FONT}" fill="#222">{html.escape(line)}</text>'
        )
    return "\n".join(parts)


def _node_svg(node: RenderNode) -> str:
    x, y, w, h = node.x, node.y, node.w, node.h
    fill, stroke, sw = node.fill, node.stroke, 1.5
    if node.kind == "store":
        ry = 6
        shape = (
            f'<path d="M{x:.1f},{y + ry:.1f} a{w / 2:.1f},{ry} 0 0 1 {w:.1f},0 v{h - 2 * ry:.1f} '
            f'a{w / 2:.1f},{ry} 0 0 1 {-w:.1f},0 z" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw}"/>'
            f'<path d="M{x:.1f},{y + ry:.1f} a{w / 2:.1f},{ry} 0 0 0 {w:.1f},0" fill="none" '
            f'stroke="{stroke}" stroke-width="{sw}"/>'
        )
    elif node.kind == "ent":
        shape = (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>'
        )
    elif node.kind == "table":
        shape = (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{LINE_H + PADY:.1f}" '
            f'fill="{stroke}" fill-opacity="0.28" stroke="{stroke}" stroke-width="{sw}"/>'
        )
    else:  # proc(コンポーネント・DFDの処理)
        shape = (
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>'
        )

    texts = []
    if node.kind == "table":
        for i, line in enumerate(node.lines):
            ty = y + PADY + LINE_H * i + 14 + (3 if i > 0 else 0)
            weight = ' font-weight="bold"' if i == 0 else ""
            family = "" if i == 0 else f" font-family='{MONO_FAMILY}'"
            texts.append(
                f'<text x="{x + PADX:.1f}" y="{ty:.1f}" font-size="{FONT}"{weight}{family} '
                f'fill="#1a1a1a">{html.escape(line)}</text>'
            )
    else:
        top = y + (h - len(node.lines) * LINE_H) / 2 + (2 if node.kind == "store" else 0)
        cx = x + w / 2
        for i, line in enumerate(node.lines):
            texts.append(
                f'<text x="{cx:.1f}" y="{top + LINE_H * i + 15:.1f}" text-anchor="middle" '
                f'fill="#1a1a1a">{html.escape(line)}</text>'
            )
    return "\n".join([shape, *texts])
