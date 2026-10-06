"""辺の経路探索(候補生成+コスト最小選択)。

出自: 別プロジェクトの自作図生成エンジンから移植。

`_cost()`の重み定数は、用途コメント付きの名前付き定数にしている。
"""

from collections.abc import Iterator

from app.services.errors import LayoutRouteNotFoundError
from app.uml.layout.geometry import gap_y, gutter_x, port_at, port_center
from app.uml.layout.model import (
    LayoutEdge,
    LayoutState,
    clean,
    seg_cross,
    seg_hits_rect,
    seg_overlap,
)

DIRS = {"t": (0, -1), "b": (0, 1), "l": (-1, 0), "r": (1, 0)}
SIDES = "tblr"
STUB = 14  # 辺から出て最初に直進する長さ(geometry.pyと同じ値。候補経路の生成にのみ使う)

# --- _cost() の重み定数(用途コメント付き) ---
_LENGTH_WEIGHT = 0.6  # 経路長1pxあたりのペナルティ
_BEND_PENALTY = 25  # 折れ(コーナー)1箇所あたりのペナルティ
_BACKWARD_EXIT_PENALTY = 60  # 出口/入口の向きが不自然な場合のペナルティ(出口・入口それぞれ)
_NODE_COLLISION_PENALTY = 100000  # 他ノードの矩形と衝突する場合のペナルティ(実質「採用しない」)
_EDGE_CROSSING_PENALTY = 400  # 既にルーティング済みの他の辺と交差する場合のペナルティ
_EDGE_OVERLAP_BASE_PENALTY = 1500  # 既にルーティング済みの他の辺と同一直線上で重なる場合の
#   基礎ペナルティ(実際は重なり長さ(px)をさらに加算する)
_SHARED_CHANNEL_PENALTY = 8  # gap/gutterのような共有通路を使う場合の軽い混雑ペナルティ


def _candidates(state: LayoutState, e: LayoutEdge, offs: dict) -> Iterator[tuple[list, tuple]]:
    """(経路点列, 選択情報)の列を返す。offsは{(node,side,edge_id): 出入口オフセット}。"""
    A, B = state.nodes[e.a], state.nodes[e.b]
    ss_list = [e.out] if e.out else list(SIDES)
    ds_list = [e.into] if e.into else list(SIDES)
    gaps = list(range(-1, state.R))
    gutters = list(range(len(state.lane_labels) + 1))
    if e.via:
        if e.via[0] == "gap":
            gaps, gutters = [e.via[1]], []
        else:
            gaps, gutters = [], [e.via[1]]
    for ss in ss_list:
        for ds in ds_list:
            P = port_at(A, ss, offs.get((e.a, ss, id(e)), 0))
            Q = port_at(B, ds, offs.get((e.b, ds, id(e)), 0))
            d1, d2 = DIRS[ss], DIRS[ds]
            P1 = (P[0] + d1[0] * STUB, P[1] + d1[1] * STUB)
            Q1 = (Q[0] + d2[0] * STUB, Q[1] + d2[1] * STUB)
            mids: list[tuple[tuple, tuple]] = []
            if P1[0] == Q1[0] or P1[1] == Q1[1]:
                mids.append(((), ("direct",)))
            mids.append((((Q1[0], P1[1]),), ("L1",)))
            mids.append((((P1[0], Q1[1]),), ("L2",)))
            for g in gaps:
                y = gap_y(state, g)
                mids.append((((P1[0], y), (Q1[0], y)), ("gap", g)))
            for i in gutters:
                x = gutter_x(state, i)
                mids.append((((x, P1[1]), (x, Q1[1])), ("gut", i)))
            for mid, tag in mids:
                pts = clean([P, P1, *mid, Q1, Q])
                if len(pts) < 2:
                    continue
                if not _valid(pts, ss, ds):
                    continue
                yield pts, (ss, ds, *tag)


def _valid(pts: list[tuple[float, float]], ss: str, ds: str) -> bool:
    """出口の向きと最初の線分が逆行しない、かつ途中で180度折り返さないことを確認する。"""
    if len(pts) >= 2:
        d = DIRS[ss]
        v = (pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
        if v[0] * d[0] + v[1] * d[1] < 0:
            return False
        d2 = DIRS[ds]
        v2 = (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
        # 入口へは辺の外側から向かって入る(辺の外向きd2と逆向きに進む)
        if v2[0] * d2[0] + v2[1] * d2[1] > 0:
            return False
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        v1 = (b[0] - a[0], b[1] - a[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        if v1[0] * v2[0] + v1[1] * v2[1] < 0:
            return False
    return True


def _cost(
    state: LayoutState, e: LayoutEdge, pts: list, choice: tuple, routed: list[LayoutEdge]
) -> float:
    A, B = state.nodes[e.a], state.nodes[e.b]
    rects = {k: (n.x, n.y, n.w, n.h) for k, n in state.nodes.items()}
    cost = 0.0
    length = sum(
        abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)
    )
    cost += length * _LENGTH_WEIGHT + (len(pts) - 2) * _BEND_PENALTY
    ss, ds = choice[0], choice[1]
    Pc, Qc = port_center(A, ss), port_center(B, ds)
    d1, d2 = DIRS[ss], DIRS[ds]
    if d1[0] * (Qc[0] - Pc[0]) + d1[1] * (Qc[1] - Pc[1]) < 0:
        cost += _BACKWARD_EXIT_PENALTY
    if d2[0] * (Pc[0] - Qc[0]) + d2[1] * (Pc[1] - Qc[1]) < 0:
        cost += _BACKWARD_EXIT_PENALTY
    n = len(pts) - 1
    for i in range(n):
        p, q = pts[i], pts[i + 1]
        for k, r in rects.items():
            if i == 0 and k == e.a:
                continue
            if i == n - 1 and k == e.b:
                continue
            if seg_hits_rect(p, q, r):
                cost += _NODE_COLLISION_PENALTY
        for o in routed:
            for j in range(len(o.pts) - 1):
                r1, s1 = o.pts[j], o.pts[j + 1]
                if seg_cross(p, q, r1, s1):
                    cost += _EDGE_CROSSING_PENALTY
                if 0 < i < n - 1 and 0 < j < len(o.pts) - 2:
                    ov = seg_overlap(p, q, r1, s1)
                    if ov > 2:
                        cost += _EDGE_OVERLAP_BASE_PENALTY + ov
    if choice[2] in ("gap", "gut"):
        cost += _SHARED_CHANNEL_PENALTY
    return cost


def route_edges(state: LayoutState, *, rounds: int = 2) -> None:
    """全辺の経路を、貪欲法(コスト最小の候補を選ぶ)でroundsラウンド決定する。
    各辺のコストは既にルーティング済みの辺に依存するため、ラウンドを重ねることで
    後段の辺のルーティング結果が前段の選択を揺り戻せるようにする。
    """
    for _ in range(rounds):
        routed: list[LayoutEdge] = []
        for e in state.edges:
            best: tuple[float, list, tuple] | None = None
            for pts, ch in _candidates(state, e, {}):
                c = _cost(state, e, pts, ch, [o for o in routed if o is not e])
                if best is None or c < best[0]:
                    best = (c, pts, ch)
            if best is None:
                raise LayoutRouteNotFoundError(f"{state.id}: 経路が見つかりません {e.a}->{e.b}")
            e.choice, e.pts = best[2], best[1]
            routed.append(e)
