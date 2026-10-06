"""描画用の中間表現(`RenderDiagram`)をdraw.io(.drawio、mxGraphのXML)に書き出す。

出自: 別プロジェクトの自作図生成エンジン`Diagram.to_drawio`をコピーし、devexで使わない
要素(レーン=swimlane・グループ枠・分岐/端子/注記ノード・破線・両端矢印)を除いた。

移植元からの変更点:
- セルのidは連番(c2, c3, ...)ではなく、要素・関係のidから作る(`n-<要素id>`/`e-<関係id>`)。
  draw.io上で意味モデルの要素と対応が取れるようにするため。draw.ioが予約する`0`/`1`とは
  接頭辞で衝突しない。
- 端点のノードを手で動かした辺(`RenderEdge.routed`がFalse)は、出入口・折れ点を書かず、
  経路を`orthogonalEdgeStyle`に任せる。
- ラベルの位置は、移植元が保存していた「線上の比率」の代わりに、経路の中点(`x=0`)からの
  オフセットとして書く(draw.ioは`x=0`の辺ラベルを経路の中点に置く)。
決定的(AI非依存)で、同じ入力からは常に同じ文字列を返す。値はすべて`html.escape`する。
"""

import html

from app.uml.export.render import RenderDiagram, RenderEdge, RenderNode, path_midpoint
from app.uml.layout.text import FONT

_BASE_NODE_STYLE = "whiteSpace=nowrap;html=1;fontSize={font};fontColor=#1a1a1a;strokeWidth=1.5;"
_NODE_SHAPE = {
    "store": "shape=cylinder3;boundedLbl=1;backgroundOutline=1;size=6;",
    "ent": "rounded=0;",
    "table": "rounded=0;align=left;verticalAlign=top;spacingLeft=8;spacingTop=4;",
    "proc": "rounded=1;arcSize=10;",
}


def to_drawio(diagram: RenderDiagram, *, diagram_id: str, title: str) -> str:
    """.drawioファイルの中身(XML文字列)を返す。"""
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    nodes = {node.id: node for node in diagram.nodes}
    cells.extend(_node_cell(node) for node in diagram.nodes)
    cells.extend(
        _edge_cell(edge, nodes[edge.source_id], nodes[edge.target_id]) for edge in diagram.edges
    )
    body = "\n        ".join(cells)
    width, height = diagram.width, diagram.height
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<mxfile host="app.diagrams.net" version="24.0.0">'
        f'<diagram id="{html.escape(diagram_id, quote=True)}" '
        f'name="{html.escape(title, quote=True)}">\n'
        f'  <mxGraphModel dx="{width:.0f}" dy="{height:.0f}" grid="1" gridSize="10" guides="1" '
        'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{width:.0f}" pageHeight="{height:.0f}" math="0" shadow="0">\n'
        f"    <root>\n        {body}\n    </root>\n  </mxGraphModel>\n</diagram></mxfile>\n"
    )


def _cell_id(prefix: str, element_id: str) -> str:
    return html.escape(f"{prefix}-{element_id}", quote=True)


def _node_cell(node: RenderNode) -> str:
    style = (
        _NODE_SHAPE.get(node.kind, _NODE_SHAPE["proc"])
        + f"fillColor={node.fill};strokeColor={node.stroke};"
        + _BASE_NODE_STYLE.format(font=FONT)
    )
    if node.kind == "table":
        head, *rows = node.lines
        value = f"<b>{html.escape(head)}</b><hr>" + "<br>".join(html.escape(r) for r in rows)
    else:
        value = "<br>".join(html.escape(line) for line in node.lines)
    return (
        f'<mxCell id="{_cell_id("n", node.id)}" value="{html.escape(value, quote=True)}" '
        f'style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{node.x:.1f}" y="{node.y:.1f}" width="{node.w:.1f}" '
        f'height="{node.h:.1f}" as="geometry"/></mxCell>'
    )


def _edge_cell(edge: RenderEdge, source: RenderNode, target: RenderNode) -> str:
    style = "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;jettySize=auto;orthogonalLoop=1;"
    waypoints = ""
    if edge.routed:
        # エンジンの経路どおりに出入りさせる(出入口はノードの左上を0、右下を1とする比率)
        (px, py), (qx, qy) = edge.points[0], edge.points[-1]
        ex, ey = (px - source.x) / source.w, (py - source.y) / source.h
        nx, ny = (qx - target.x) / target.w, (qy - target.y) / target.h
        style += (
            f"exitX={ex:.4f};exitY={ey:.4f};exitDx=0;exitDy=0;"
            f"entryX={nx:.4f};entryY={ny:.4f};entryDx=0;entryDy=0;"
        )
        inner = "".join(f'<mxPoint x="{x:.1f}" y="{y:.1f}"/>' for x, y in edge.points[1:-1])
        waypoints = f'<Array as="points">{inner}</Array>' if inner else ""
    style += "endArrow=block;endFill=1;" if edge.arrow else "endArrow=none;"
    style += "strokeColor=#444444;strokeWidth=1.6;fontSize=14;labelBackgroundColor=#ffffff;"

    offset = ""
    if edge.label and edge.routed and edge.label_pos is not None:
        mx, my = path_midpoint(edge.points)
        cx, cy = edge.label_pos
        offset = f'<mxPoint x="{cx - mx:.1f}" y="{cy - my:.1f}" as="offset"/>'
    label = html.escape(
        "<br>".join(html.escape(line) for line in edge.label.split("\n")), quote=True
    )
    return (
        f'<mxCell id="{_cell_id("e", edge.id)}" value="{label}" style="{style}" '
        'edge="1" parent="1" '
        f'source="{_cell_id("n", edge.source_id)}" target="{_cell_id("n", edge.target_id)}">'
        f'<mxGeometry x="0" relative="1" as="geometry">{waypoints}{offset}</mxGeometry></mxCell>'
    )
