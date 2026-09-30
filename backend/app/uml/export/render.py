"""意味モデル+配置+辺ラベルから、描画用の中間表現(`RenderDiagram`)を組み立てる純粋関数。

SVG(`svg.py`)とdraw.io(`drawio.py`)は同じ中間表現から書き出す。こうして
「何を描くか(ノードの行・色・辺の経路・ラベル)」をここで1回だけ決め、
「どう書くか(SVGの要素/mxCell)」を各出力に任せる。

- ノードの行: 配置(`LayoutModel`)は折り返し後の行を保存していないため、ここで折り返し直す
  (`table`は改行区切りのまま。レイアウト計算の`geometry.size_nodes`と同じ規則)。
- 辺の経路: エンジンが計算した折れ点があればそれを使う。端点のノードを手で動かした辺
  (`points=[]`、D2)は、ここで簡易な直交経路(Z字またはL字)を作る。draw.ioは自前で
  経路を引ける(`orthogonalEdgeStyle`)が、SVGには描画を任せる相手が無いため。
  この簡易経路はノードを避けない(既知の制約。自動レイアウトを再実行すれば解消する)。
- レーン帯は描かない(レビュー画面にも無いため、承認した見た目と揃える)。
"""

from collections.abc import Mapping
from dataclasses import dataclass

from app.services.errors import UmlLayoutRequiredError
from app.uml.domain import ComponentSemanticModel, DfdSemanticModel, ErSemanticModel
from app.uml.layout import LayoutModel, element_kind, element_text
from app.uml.layout.geometry import PADX
from app.uml.layout.model import LayoutBox, Point, clean
from app.uml.layout.text import wrap

_AnySemanticModel = ComponentSemanticModel | ErSemanticModel | DfdSemanticModel

# 色: (塗り, 枠)。出自: 移植元エンジンのPALETTEから、devexの4種のノードで使うものだけ
PALETTE: dict[str, tuple[str, str]] = {
    "default": ("#f4f6fb", "#7f89b0"),
    "pg": ("#dbe9fb", "#3b6fa8"),
    "ext": ("#efefef", "#8c8c8c"),
}
# ノード種別 → 色(外部実体は灰、データストアとテーブルは永続化の青、処理・モジュールは既定)
_KIND_COLOR = {"proc": "default", "ent": "ext", "store": "pg", "table": "pg"}


@dataclass(frozen=True)
class RenderNode:
    id: str
    kind: str  # "proc" / "ent" / "store" / "table"(app/uml/layout/model.py LayoutNode.kind)
    x: float
    y: float
    w: float
    h: float
    lines: list[str]  # 表示する行(table は1行目がテーブル名、以降がカラム)
    fill: str
    stroke: str


@dataclass(frozen=True)
class RenderEdge:
    id: str
    source_id: str
    target_id: str
    points: list[Point]  # 描く経路(始点・折れ点・終点)
    routed: bool  # True: エンジンの経路 / False: ここで作った簡易経路(draw.ioは自前で引く)
    label: str
    label_pos: Point | None  # ラベルの中心。None なら経路の中点に置く
    arrow: bool  # 終点に矢印を付けるか(ERは多重度のラベルで表すので付けない)


@dataclass(frozen=True)
class RenderDiagram:
    width: float
    height: float
    nodes: list[RenderNode]
    edges: list[RenderEdge]


def build_render(
    model: _AnySemanticModel,
    layout: LayoutModel,
    label_texts: Mapping[str, str],
) -> RenderDiagram:
    """描画用の中間表現を組み立てる。配置の無い要素があれば`UmlLayoutRequiredError`
    (承認の条件で弾いているため、通常は起きない)。"""
    missing = [el.id for el in model.elements if el.id not in layout.nodes]
    if missing:
        raise UmlLayoutRequiredError(f"配置の無い要素があります({', '.join(missing)})")

    nodes: list[RenderNode] = []
    for el in model.elements:
        box = layout.nodes[el.id]
        kind = element_kind(model.notation, el)
        fill, stroke = PALETTE[_KIND_COLOR[kind]]
        nodes.append(
            RenderNode(
                id=el.id,
                kind=kind,
                x=box.x,
                y=box.y,
                w=box.w,
                h=box.h,
                lines=_lines(kind, element_text(model.notation, el), box.w),
                fill=fill,
                stroke=stroke,
            )
        )

    edges: list[RenderEdge] = []
    for rel in model.relations:
        geometry = layout.edges.get(rel.id)
        routed = geometry is not None and len(geometry.points) >= 2
        points = (
            list(geometry.points)
            if geometry is not None and routed
            else orthogonal_fallback(layout.nodes[rel.source_id], layout.nodes[rel.target_id])
        )
        edges.append(
            RenderEdge(
                id=rel.id,
                source_id=rel.source_id,
                target_id=rel.target_id,
                points=points,
                routed=routed,
                label=label_texts.get(rel.id, ""),
                label_pos=geometry.label_pos if geometry is not None and routed else None,
                arrow=model.notation != "er",
            )
        )
    return RenderDiagram(width=layout.width, height=layout.height, nodes=nodes, edges=edges)


def _lines(kind: str, text: str, width: float) -> list[str]:
    if kind == "table":
        return text.split("\n")
    # 配置のノード幅は「最も長い行 + 左右の余白」なので、その幅で折り返せば同じ行に戻る
    # (浮動小数の誤差で1文字あふれないよう、0.5pxの余裕を持たせる)
    return wrap(text, width - 2 * PADX + 0.5)


def orthogonal_fallback(a: LayoutBox, b: LayoutBox) -> list[Point]:
    """ノードaからbへの簡易な直交経路。向かい合う辺の中点どうしを、中間で1回曲がる
    Z字(または一直線)で結ぶ。左右に離れていれば横から、そうでなければ上下から出入りする。"""
    acx, acy = a.x + a.w / 2, a.y + a.h / 2
    bcx, bcy = b.x + b.w / 2, b.y + b.h / 2
    apart_x = a.x + a.w <= b.x or b.x + b.w <= a.x
    apart_y = a.y + a.h <= b.y or b.y + b.h <= a.y
    if apart_x and (not apart_y or abs(bcx - acx) >= abs(bcy - acy)):
        ax = a.x + a.w if bcx > acx else a.x
        bx = b.x if bcx > acx else b.x + b.w
        mx = (ax + bx) / 2
        return clean([(ax, acy), (mx, acy), (mx, bcy), (bx, bcy)])
    ay = a.y + a.h if bcy >= acy else a.y
    by = b.y if bcy >= acy else b.y + b.h
    my = (ay + by) / 2
    return clean([(acx, ay), (acx, my), (bcx, my), (bcx, by)])


def path_midpoint(points: list[Point]) -> Point:
    """経路の長さの中点(ラベルの既定位置。draw.ioも辺ラベルの基準をこの点に置く)。"""
    lengths = [
        abs(q[0] - p[0]) + abs(q[1] - p[1]) for p, q in zip(points, points[1:], strict=False)
    ]
    half = sum(lengths) / 2
    for (p, q), length in zip(zip(points, points[1:], strict=False), lengths, strict=True):
        if half <= length and length > 0:
            t = half / length
            return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
        half -= length
    return points[-1]
