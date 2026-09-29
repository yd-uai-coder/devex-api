"""経路の仕上げ処理(出入口オフセット・通路スロット割当・ラベル配置・指標計測)。

出自: 別プロジェクトの自作図生成エンジンから移植。
"""

import itertools

from app.uml.layout.geometry import gap_y, gutter_x, place_rows, port_at
from app.uml.layout.model import (
    LayoutEdge,
    LayoutState,
    clean,
    seg_cross,
    seg_hits_rect,
    seg_overlap,
)
from app.uml.layout.routing import DIRS, STUB
from app.uml.layout.text import tw

LINE_H = 20  # 1行の高さ(geometry.pyと同じ値。ラベルサイズの見積もりに使う)


def _assign_ports(state: LayoutState) -> dict:
    """同じノード・同じ側に付く辺をグループ化し(`state.port_groups`)、出入口オフセットを返す。
    グループ内の並び順は、相手ノードの位置(横方向のポートなら相手のcx、縦方向ならcy)で
    ソートする(交差を減らす初期並び)。"""
    groups: dict[tuple, list] = {}
    for e in state.edges:
        assert e.choice is not None, "routing.route_edgesの後に呼ぶ前提(choiceが未確定)"
        ss, ds = e.choice[0], e.choice[1]
        groups.setdefault((e.a, ss), []).append((e, "a"))
        groups.setdefault((e.b, ds), []).append((e, "b"))
    for (_nid, side), items in groups.items():

        def key(it, side=side):
            edge, end = it
            other = state.nodes[edge.b if end == "a" else edge.a]
            return other.cx if side in "tb" else other.cy

        items.sort(key=key)
    state.port_groups = groups
    return _offs_from_groups(state)


def _offs_from_groups(state: LayoutState) -> dict:
    """`state.port_groups`の並び順から、辺ごとの出入口オフセット(ノード辺上の位置)を計算する。"""
    offs: dict[tuple, float] = {}
    for (nid, side), items in state.port_groups.items():
        nd = state.nodes[nid]
        k = len(items)
        if k == 1:
            offs[(nid, side, id(items[0][0]))] = 0
            continue
        extent = nd.w if side in "tb" else nd.h
        spacing = min(20.0, max(8.0, (extent - 20) / (k - 1)))
        for i, (e, _end) in enumerate(items):
            offs[(nid, side, id(e))] = spacing * (i - (k - 1) / 2)
    return offs


def _port_score(state: LayoutState, slots: dict) -> float:
    reroute_final(state, _offs_from_groups(state), slots)
    crossings, overlaps, _collisions = count_metrics(state)
    return crossings + overlaps


def optimize_ports(state: LayoutState, slots: dict) -> None:
    """同じ辺に付く辺の並び順を入れ替え、交差・重なりが最小になる並びを選ぶ。
    4本以下は全順列、5本以上は隣同士の入れ替えの山登り(全順列だと5!=120通り以上になり
    非現実的なため)。"""
    for key, items in state.port_groups.items():
        k = len(items)
        if k < 2:
            continue
        if k <= 4:
            best: tuple[float, list] | None = None
            for perm in itertools.permutations(items):
                state.port_groups[key] = list(perm)
                sc = _port_score(state, slots)
                if best is None or sc < best[0]:
                    best = (sc, list(perm))
            assert best is not None
            state.port_groups[key] = best[1]
        else:
            cur = _port_score(state, slots)
            improved = True
            while improved and cur > 0:
                improved = False
                for i in range(k - 1):
                    seq = state.port_groups[key]
                    seq[i], seq[i + 1] = seq[i + 1], seq[i]
                    sc = _port_score(state, slots)
                    if sc < cur:
                        cur, improved = sc, True
                    else:
                        seq[i], seq[i + 1] = seq[i + 1], seq[i]
    reroute_final(state, _offs_from_groups(state), slots)


def optimize_slots(state: LayoutState, slots: dict) -> None:
    """同じ通路(gap/gut)を通る辺どうしのスロット(並び)を入れ替え、交差を減らす。"""
    buckets: dict[tuple, list[LayoutEdge]] = {}
    for e in state.edges:
        assert e.choice is not None
        tag = e.choice[2:]
        if tag[0] in ("gap", "gut"):
            buckets.setdefault(tag, []).append(e)

    def score() -> float:
        reroute_final(state, _offs_from_groups(state), slots)
        crossings, overlaps, _collisions = count_metrics(state)
        return crossings * 10 + overlaps

    cur = score()
    for es in buckets.values():
        improved = len(es) >= 2
        while improved and cur > 0:
            improved = False
            for a, b in itertools.combinations(es, 2):
                ka, kb = slots.get(id(a), 0), slots.get(id(b), 0)
                if ka == kb:
                    continue
                slots[id(a)], slots[id(b)] = kb, ka
                sc = score()
                if sc < cur:
                    cur, improved = sc, True
                else:
                    slots[id(a)], slots[id(b)] = ka, kb
    score()


def assign_slots(state: LayoutState, offs: dict) -> tuple[dict, dict[int, int]]:
    """同じ通路(gap/gut)を通る線分が重ならないようにスロットを割り当てる
    (区間スケジューリングの貪欲法)。gap側は、割り当てたスロット数を`gap_extra`として返し、
    `geometry.place_rows`がその行の下の余白を広げるのに使う。"""
    buckets: dict[tuple, list[tuple[float, float, LayoutEdge]]] = {}
    for e in state.edges:
        assert e.choice is not None
        tag = e.choice[2:]
        if tag[0] not in ("gap", "gut"):
            continue
        A, B = state.nodes[e.a], state.nodes[e.b]
        P = port_at(A, e.choice[0], offs.get((e.a, e.choice[0], id(e)), 0))
        Q = port_at(B, e.choice[1], offs.get((e.b, e.choice[1], id(e)), 0))
        lo, hi = sorted((P[0], Q[0])) if tag[0] == "gap" else sorted((P[1], Q[1]))
        buckets.setdefault(tag, []).append((lo, hi, e))

    slots: dict[int, int] = {}
    gap_extra: dict[int, int] = {}
    for tag, items in buckets.items():
        items.sort(key=lambda t: (t[0], t[1]))
        ends: list[float] = []
        for lo, hi, e in items:
            for k, end in enumerate(ends):
                if lo > end + 6:
                    ends[k] = hi
                    slots[id(e)] = k
                    break
            else:
                ends.append(hi)
                slots[id(e)] = len(ends) - 1
        if tag[0] == "gap":
            gap_extra[tag[1]] = max(gap_extra.get(tag[1], 0), len(ends))
    return slots, gap_extra


def reroute_final(state: LayoutState, offs: dict, slots: dict) -> None:
    """出入口オフセット・通路スロットが確定した後、各辺の最終的な点列を組み立てる。"""
    for e in state.edges:
        assert e.choice is not None
        ss, ds = e.choice[0], e.choice[1]
        tag = e.choice[2:]
        A, B = state.nodes[e.a], state.nodes[e.b]
        P = port_at(A, ss, offs.get((e.a, ss, id(e)), 0))
        Q = port_at(B, ds, offs.get((e.b, ds, id(e)), 0))
        d1, d2 = DIRS[ss], DIRS[ds]
        P1 = (P[0] + d1[0] * STUB, P[1] + d1[1] * STUB)
        Q1 = (Q[0] + d2[0] * STUB, Q[1] + d2[1] * STUB)
        k = slots.get(id(e), 0)
        if tag[0] == "direct":
            if abs(P1[0] - Q1[0]) < 0.5 or abs(P1[1] - Q1[1]) < 0.5:
                mid: tuple = ()
            elif ss in "lr":  # 横向きに出入りしている: 中間の縦線で段差をつける
                xm = (P1[0] + Q1[0]) / 2
                mid = ((xm, P1[1]), (xm, Q1[1]))
            else:
                ym = (P1[1] + Q1[1]) / 2
                mid = ((P1[0], ym), (Q1[0], ym))
        elif tag[0] == "L1":
            mid = ((Q1[0], P1[1]),)
        elif tag[0] == "L2":
            mid = ((P1[0], Q1[1]),)
        elif tag[0] == "gap":
            y = gap_y(state, tag[1], k)
            mid = ((P1[0], y), (Q1[0], y))
        else:  # gut: 縦の通路は境界線を中心に左右へ10pxずつ広げる(0, +14, -14, +28, ...)
            x = gutter_x(state, tag[1]) + ((k + 1) // 2) * 14 * (1 if k % 2 else -1)
            mid = ((x, P1[1]), (x, Q1[1]))
        e.pts = clean([P, P1, *mid, Q1, Q])


# ---- ラベル配置
def _label_size(text: str) -> tuple[float, float]:
    lines = text.split("\n")
    return max(tw(line) for line in lines) + 10, LINE_H * len(lines)


def place_labels(state: LayoutState) -> None:
    """各辺のラベル位置を決める(現時点でdevexのドメインモデルに辺ラベルは無いため、
    `e.label`が空文字の辺では何もしない。将来ラベルを持つnotationが追加された場合に
    そのまま機能する)。"""
    placed: list[tuple] = []
    rects = [(n.x, n.y, n.w, n.h) for n in state.nodes.values()]
    for e in state.edges:
        if not e.label:
            continue
        lw, lh = _label_size(e.label)
        segs = []
        total = 0.0
        for i in range(len(e.pts) - 1):
            p, q = e.pts[i], e.pts[i + 1]
            ln = abs(q[0] - p[0]) + abs(q[1] - p[1])
            segs.append((ln, i, total))
            total += ln
        best: tuple | None = None
        for ln, i, _start in sorted(segs, key=lambda t: -t[0]):
            p, q = e.pts[i], e.pts[i + 1]
            horiz = p[1] == q[1]
            for t in (0.5, 0.3, 0.7):
                mx = p[0] + (q[0] - p[0]) * t
                my = p[1] + (q[1] - p[1]) * t
                cands = (
                    [(mx, my - lh / 2 - 3), (mx, my + lh / 2 + 3)]
                    if horiz
                    else [(mx + lw / 2 + 6, my), (mx - lw / 2 - 6, my)]
                )
                for cx_, cy_ in cands:
                    r = (cx_ - lw / 2, cy_ - lh / 2, lw, lh)
                    hit = sum(1 for o in rects if _rect_hit(r, o, 1)) * 100
                    hit += sum(1 for o in placed if _rect_hit(r, o, 2)) * 100
                    for oe in state.edges:
                        if oe is e:
                            continue
                        for j in range(len(oe.pts) - 1):
                            if seg_hits_rect(oe.pts[j], oe.pts[j + 1], r, infl=0):
                                hit += 15
                    if horiz and ln < lw * 0.55:
                        hit += 30  # 線分が短すぎてラベルが線から大きくはみ出す
                    if best is None or hit < best[0]:
                        best = (hit, (cx_, cy_, r))
                if best and best[0] == 0:
                    break
            if best and best[0] == 0:
                break
        assert best is not None
        e.label_pos = best[1]
        if best[0] >= 100:
            state.report.setdefault("label_fallback", []).append(f"{e.a}->{e.b}:{e.label}")
        placed.append(best[1][2])


def _rect_hit(a: tuple, b: tuple, pad: float = 0) -> bool:
    return not (
        a[0] + a[2] + pad < b[0]
        or b[0] + b[2] + pad < a[0]
        or a[1] + a[3] + pad < b[1]
        or b[1] + b[3] + pad < a[1]
    )


# ---- 指標
def count_metrics(state: LayoutState) -> tuple[int, int, int]:
    """(交差数, 重なり数, 衝突数)を数える。"""
    crossings = 0
    overlaps = 0
    collisions = 0
    state.cross_pairs = []
    rects = {k: (n.x, n.y, n.w, n.h) for k, n in state.nodes.items()}
    for i, e in enumerate(state.edges):
        n = len(e.pts) - 1
        for s in range(n):
            for k, r in rects.items():
                if s == 0 and k == e.a:
                    continue
                if s == n - 1 and k == e.b:
                    continue
                if seg_hits_rect(e.pts[s], e.pts[s + 1], r, infl=1):
                    collisions += 1
        for o in state.edges[i + 1 :]:
            for s in range(n):
                for t in range(len(o.pts) - 1):
                    if seg_cross(e.pts[s], e.pts[s + 1], o.pts[t], o.pts[t + 1]):
                        crossings += 1
                        state.cross_pairs.append(f"{e.a}->{e.b} x {o.a}->{o.b}")
                    in_interior = 0 < s < n - 1 and 0 < t < len(o.pts) - 2
                    overlap_len = seg_overlap(e.pts[s], e.pts[s + 1], o.pts[t], o.pts[t + 1])
                    if in_interior and overlap_len > 2:
                        overlaps += 1
    return crossings, overlaps, collisions


def measure(state: LayoutState) -> None:
    crossings, overlaps, collisions = count_metrics(state)
    state.report.update(
        id=state.id,
        w=round(state.width),
        h=round(state.height),
        nodes=len(state.nodes),
        edges=len(state.edges),
        crossings=crossings,
        overlaps=overlaps,
        collisions=collisions,
    )


def finalize(state: LayoutState) -> None:
    """`routing.route_edges`後の辺の点列を仕上げる(出入口オフセット→通路スロット→
    再ルーティング、を2周した上でポート順序・スロット順序を最適化し、ラベル配置・指標計測まで行う)。
    """
    slots: dict = {}
    for _ in range(2):
        offs = _assign_ports(state)
        slots, gap_extra = assign_slots(state, offs)
        place_rows(state, gap_extra)
        reroute_final(state, offs, slots)
    optimize_ports(state, slots)
    optimize_slots(state, slots)
    place_labels(state)
    measure(state)
