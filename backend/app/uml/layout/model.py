"""レイアウト計算の内部表現(ノード・辺・計算中の状態)と、
`uml_diagrams.layout_model`へ永続化する出力スキーマ(Pydantic)。
出自: 別プロジェクトの自作図生成エンジンから移植。

描画用の見た目属性(色・破線・矢印の向き等)は`app/uml/export/`
(draw.io Generator/SVG出力)の関心事であり、レイアウト(座標計算)自体には
不要なため移植しない。`kind`はノードのサイズ計算・ポート位置計算(`geometry.py`)に影響するため残す。
"""

from dataclasses import dataclass, field

from pydantic import BaseModel

MARGIN = 10  # 図の外周余白
MAX_W = 960  # 図の最大幅(px)

Point = tuple[float, float]


@dataclass
class LayoutNode:
    """レイアウト計算中の1ノード。

    kind: "proc"(コンポーネント/DFD処理)/ "ent"(DFD外部実体)/ "store"(DFDデータストア)/
    "table"(ERテーブル)。ノードの見積もりサイズ・ポート形状に影響する。
    """

    id: str
    lane: int
    row: int
    text: str
    kind: str = "proc"
    w_hint: float | None = None
    lines: list[str] = field(default_factory=list)
    w: float = 0.0
    h: float = 0.0
    x: float = 0.0
    y: float = 0.0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class LayoutEdge:
    """レイアウト計算中の1辺。"""

    id: str
    a: str
    b: str
    label: str = ""
    out: str | None = None  # 出る辺の指定(t/b/l/r)
    into: str | None = None  # 入る辺の指定
    via: tuple | None = None  # 通路の指定: ("gap", g) / ("gut", i)
    # 以下、ルーティング結果
    choice: tuple | None = None
    pts: list = field(default_factory=list)
    label_pos: tuple | None = None


class LayoutState:
    """レイアウト計算中の可変状態一式。

    幾何計算・経路探索・仕上げ処理を1つのクラスのメソッドにまとめず、
    CLAUDE.md #30の依存順ファイル分割(章ごとに1ファイル)に合わせ、この状態を
    受け取る関数群として`geometry.py`/`routing.py`/`finalize.py`/`crossing_reduction.py`に
    分割する(`self.method()` → `function(state)`という書き換え。アルゴリズム自体は不変)。
    """

    def __init__(
        self,
        diagram_id: str,
        lane_labels: list[str | None],
        nodes: dict[str, LayoutNode],
        edges: list[LayoutEdge],
    ):
        self.id = diagram_id
        self.lane_labels = lane_labels
        self.nodes = nodes
        self.edges = edges
        self.report: dict = {}
        # geometry.pyの各関数が計算する属性(初期値は未計算を表すプレースホルダー)
        self.lane_x: list[tuple[float, float]] = []
        self.width: float = 0.0
        self.height: float = 0.0
        self.R: int = 0
        self.rowh: dict[int, float] = {}
        self.gap: dict[int, float] = {}
        self.top_of_row: dict[int, float] = {}
        self.lanes_bottom: float = 0.0
        self.cross_pairs: list[str] = []
        # finalize.pyが計算する、辺の出入口グループ({(node_id,side): [(edge,"a"|"b"), ...]})
        self.port_groups: dict[tuple, list] = {}


Rect = tuple[float, float, float, float]


def seg_hits_rect(p: Point, q: Point, r: Rect, infl: float = 5) -> bool:
    """軸平行線分pqが矩形r(x,y,w,h)(inflだけ膨らませる)と交わるか。"""
    x0, y0, x1, y1 = r[0] - infl, r[1] - infl, r[0] + r[2] + infl, r[1] + r[3] + infl
    (ax, ay), (bx, by) = p, q
    lx, hx = min(ax, bx), max(ax, bx)
    ly, hy = min(ay, by), max(ay, by)
    return not (hx < x0 or lx > x1 or hy < y0 or ly > y1)


def seg_cross(p: Point, q: Point, r: Point, s: Point) -> bool:
    """垂直な2線分が両方の内部で交差するか(端点での接触は数えない)。"""
    pv, rv = p[0] == q[0], r[0] == s[0]
    if pv == rv:
        return False
    if not pv:
        p, q, r, s = r, s, p, q
    x = p[0]  # p-qが垂直、r-sが水平
    y = r[1]
    return (
        min(r[0], s[0]) + 1 < x < max(r[0], s[0]) - 1
        and min(p[1], q[1]) + 1 < y < max(p[1], q[1]) - 1
    )


def seg_overlap(p: Point, q: Point, r: Point, s: Point) -> float:
    """同一直線上の2線分の重なり長さ。"""
    if p[0] == q[0] == r[0] == s[0]:
        lo = max(min(p[1], q[1]), min(r[1], s[1]))
        hi = min(max(p[1], q[1]), max(r[1], s[1]))
        return max(0.0, hi - lo)
    if p[1] == q[1] == r[1] == s[1]:
        lo = max(min(p[0], q[0]), min(r[0], s[0]))
        hi = min(max(p[0], q[0]), max(r[0], s[0]))
        return max(0.0, hi - lo)
    return 0.0


def clean(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """重複点と一直線上の中間点を取り除く。"""
    out: list[tuple[float, float]] = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 0.01 and abs(out[-1][1] - p[1]) < 0.01:
            continue
        out.append((round(p[0], 2), round(p[1], 2)))
    changed = True
    while changed and len(out) > 2:
        changed = False
        for i in range(1, len(out) - 1):
            a, b, c = out[i - 1], out[i], out[i + 1]
            if (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]):
                del out[i]
                changed = True
                break
    return out


# ---------------------------------------------------------------- 出力スキーマ
class LayoutBox(BaseModel):
    """1ノードの計算済みジオメトリ(`uml_diagrams.layout_model.nodes[element_id]`)。"""

    x: float
    y: float
    w: float
    h: float
    lane: int
    row: int


class LayoutEdgeGeometry(BaseModel):
    """1辺の計算済み経路(`uml_diagrams.layout_model.edges[relation_id]`)。

    `points`が空リストのときは「折れ点なし」を意味する。レビュー画面でユーザーが端点の
    ノードを手で動かした辺は、エンジンが計算した経路が合わなくなるため折れ点を捨て、
    描画を React Flow の smoothstep / draw.io の `orthogonalEdgeStyle` に任せる。
    """

    points: list[tuple[float, float]]
    # ラベル(ERの多重度・DFDのデータ項目名)の中心座標。ラベルの無い辺、配置を計算した後に
    # 端点のノードを手で動かした辺(points=[])、ラベル位置を持たない古い配置ではNone
    label_pos: tuple[float, float] | None = None


class LayoutMetrics(BaseModel):
    """交差数・重なり数・衝突数(見づらさを主観でなく測定するための指標)。

    いずれも辺(線)についての指標である。`overlaps`は線どうしが同じ区間を重ねて走る数、
    `collisions`は線がノードの矩形を横切る数で、ノードどうしの重なりは数えない
    (ノードが重ならないことは`ranking.py`の行の割り当てで保証する)。
    """

    crossings: int
    overlaps: int
    collisions: int


class LayoutModel(BaseModel):
    """`uml_diagrams.layout_model`カラムへそのまま保存するトップレベルのスキーマ。"""

    width: float
    height: float
    nodes: dict[str, LayoutBox]
    edges: dict[str, LayoutEdgeGeometry]
    metrics: LayoutMetrics
