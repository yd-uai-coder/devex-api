"""行の入れ替えによる交差削減。出自: 別プロジェクトの自作図生成エンジンから移植。

行を人が手で配置する図なら、その意図を守るために「入れ替えてよいノード」を明示的に
宣言させる必要がある。devexでは行(row)自体が`ranking.py`によって全て自動算出されるため、
保護すべき「人の意図」が無い。よって**全要素を交差削減の対象にする**。

**評価の軽量化**: 移植したときの山登りは、行を1回入れ替えるたびに経路探索と
仕上げ(`pipeline.route`)の全体を走らせて評価し、繰り返しの回数にも上限が無かった。DFDの要素と線が
増えると数分かかった(9要素・18本で65〜106秒、11要素・23本で158〜245秒)。そこで次の2段に分けた。

1. **近似で並べる**(`sketch_score`): 実際の経路を作らず、「ノードの中心(レーン番号, 行番号)を
   結ぶ線分」の交差数と、線分が他のノードを貫く数で評価する(Sugiyama法の交差削減と同じく、行の順
   だけで決まる近似)。1回の評価が経路探索より3桁以上軽い。
2. **実際の経路で仕上げる**(`quick_route_score`): 近似は直交の経路での交差を正確には予測しない
   ため、経路探索を1巡だけ行う簡易な評価で、時間の上限まで山登りを続ける。

どちらの山登りにも、繰り返しの回数(`MAX_PASSES`)と時間(`SKETCH_BUDGET_SECONDS`・
`REFINE_BUDGET_SECONDS`)の上限を置く。経路探索の全体は最後に1回だけ行う。
"""

import itertools
import time
from collections.abc import Callable

from app.uml.layout.finalize import count_metrics
from app.uml.layout.geometry import compute_lane_geometry, place_rows, size_nodes
from app.uml.layout.model import LayoutState
from app.uml.layout.pipeline import route
from app.uml.layout.routing import route_edges

# 実際の経路での評価の重み付け(交差の削減を最優先し、重なりは副次的な指標として扱う)。
_CROSSING_WEIGHT = 1000
_OVERLAP_WEIGHT = 300
_SCORE_THRESHOLD = 1000  # 実際の経路での評価がこれ未満(交差0)まで下がったら十分とみなす

# 近似の評価の重み付け: 線分がノードを貫くと、実際の経路では迂回か重なりになるため、
# 交差1つより重く数える。
_SKETCH_CROSSING_WEIGHT = 1
_SKETCH_THROUGH_NODE_WEIGHT = 2
# 線分がノードを貫くとみなす、ノードの中心からの距離(レーン・行の1単位に対する割合)
_NODE_RADIUS = 0.35

MAX_PASSES = 20
SKETCH_BUDGET_SECONDS = 3.0  # 近似の評価での山登りの時間の上限
REFINE_BUDGET_SECONDS = 15.0  # 実際の経路での仕上げの山登りの時間の上限

Point = tuple[float, float]


def quick_route_score(state: LayoutState) -> float:
    """実際の経路での評価の簡易版。経路探索を1巡だけ行い(本番は2巡)、仕上げ(ポート・通路の
    最適化、ラベル配置)を省いて、交差と重なりを数える。仕上げの前後で交差数はほとんど変わらず、
    所要時間は全体の3分の1程度になる。"""
    state.report = {}
    compute_lane_geometry(state)
    size_nodes(state)
    place_rows(state)
    route_edges(state, rounds=1)
    crossings, overlaps, _collisions = count_metrics(state)
    return crossings * _CROSSING_WEIGHT + overlaps * _OVERLAP_WEIGHT


def _cross(p: Point, q: Point, r: Point, s: Point) -> bool:
    """2本の線分が内部で交差するか(端点を共有する・同一直線上に重なる場合は数えない)。"""

    def orient(a: Point, b: Point, c: Point) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2 = orient(p, q, r), orient(p, q, s)
    d3, d4 = orient(r, s, p), orient(r, s, q)
    return d1 * d2 < 0 and d3 * d4 < 0


def _passes_through(p: Point, q: Point, c: Point) -> bool:
    """線分pqが、点cを中心とするノードを貫くか(端点のノードは呼び出し側で除く)。"""
    (px, py), (qx, qy), (cx, cy) = p, q, c
    dx, dy = qx - px, qy - py
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return False
    t = ((cx - px) * dx + (cy - py) * dy) / length2
    if not 0 < t < 1:
        return False
    nx, ny = px + t * dx - cx, py + t * dy - cy
    return nx * nx + ny * ny < _NODE_RADIUS * _NODE_RADIUS


def sketch_score(state: LayoutState) -> float:
    """ノードの中心(レーン番号, 行番号)を結ぶ線分で見積もった、配置の悪さ(小さいほど良い)。
    経路探索をしないため、行を入れ替えるたびに呼んでも軽い。"""
    centers = {nid: (float(n.lane), float(n.row)) for nid, n in state.nodes.items()}
    segments = [
        (e.a, e.b, centers[e.a], centers[e.b])
        for e in state.edges
        if e.a in centers and e.b in centers and e.a != e.b
    ]
    crossings = 0
    for (a1, b1, p, q), (a2, b2, r, s) in itertools.combinations(segments, 2):
        if {a1, b1} & {a2, b2}:
            continue
        if _cross(p, q, r, s):
            crossings += 1
    through = sum(
        1
        for a, b, p, q in segments
        for nid, c in centers.items()
        if nid not in (a, b) and _passes_through(p, q, c)
    )
    return crossings * _SKETCH_CROSSING_WEIGHT + through * _SKETCH_THROUGH_NODE_WEIGHT


def _hill_climb(
    state: LayoutState,
    score: Callable[[LayoutState], float],
    deadline: float,
    *,
    good_enough: float = 0,
) -> float:
    """同じレーンの要素同士で行を入れ替える山登り。(1) ペアの行を交換して改善するか試す、
    (2) 各要素を同じレーン内の空いている行へ移動して改善するか試す、を改善が止まるか、
    回数・時間の上限に達するまで繰り返す。最良の評価を返す(`state`の行は最良の配置になる)。"""
    ids = sorted(state.nodes)
    best = score(state)
    for _ in range(MAX_PASSES):
        if best <= good_enough or time.monotonic() > deadline:
            return best
        improved = False
        for a, b in itertools.combinations(ids, 2):
            na, nb = state.nodes[a], state.nodes[b]
            if na.lane != nb.lane:
                continue
            na.row, nb.row = nb.row, na.row
            sc = score(state)
            if sc < best:
                best, improved = sc, True
            else:
                na.row, nb.row = nb.row, na.row
            if time.monotonic() > deadline:
                return best
        # 同じレーンの空いている行への移動も試す
        maxrow = max(n.row for n in state.nodes.values()) + 1
        for a in ids:
            na = state.nodes[a]
            taken = {n.row for n in state.nodes.values() if n.lane == na.lane and n is not na}
            old = na.row
            for r in range(maxrow + 1):
                if r == old or r in taken:
                    continue
                na.row = r
                sc = score(state)
                if sc < best:
                    best, improved, old = sc, True, r
                else:
                    na.row = old
                if time.monotonic() > deadline:
                    return best
        if not improved:
            return best
    return best


def optimize(state: LayoutState) -> None:
    """行の順を、(1) 近似の評価(`sketch_score`)で並べ、(2) 実際の経路の簡易な評価
    (`quick_route_score`)での山登りで、時間の上限まで仕上げる。(1)で並べた配置が元の配置より
    悪ければ、元の配置から(2)を始める。経路探索の全体(`pipeline.route`)は最後の1回だけ行う。"""
    original_rows = {nid: n.row for nid, n in state.nodes.items()}
    original = quick_route_score(state)
    if original >= _SCORE_THRESHOLD:
        _hill_climb(state, sketch_score, time.monotonic() + SKETCH_BUDGET_SECONDS)
        if quick_route_score(state) > original:
            for nid, row in original_rows.items():
                state.nodes[nid].row = row
        _hill_climb(
            state,
            quick_route_score,
            time.monotonic() + REFINE_BUDGET_SECONDS,
            good_enough=_SCORE_THRESHOLD - 1,
        )
    route(state)  # 最良の配置で、経路探索と仕上げの全体を1回だけ行う
