"""レーン幅・ノードサイズ・行位置・ポート座標の計算。

出自: 別プロジェクトの自作図生成エンジンから移植。
devexにはレーンごとの幅の重み・ヒントに相当する入力が無いため、全レーンを均等幅として扱う。

幅960px超過は入力次第で実行時に起こり得るため、`assert`ではなく
`LayoutWidthExceededError`として送出する(Phase-7-4.md申し送り#1: 4xxへ変換可能な専用例外化)。
"""

from app.services.errors import LayoutWidthExceededError
from app.uml.layout.model import MARGIN, MAX_W, LayoutNode, LayoutState
from app.uml.layout.text import tw, wrap

TOP = 10  # レーン上端(レーン見出しの描画余白として、Phase 12のexportが使う)
HEAD_H = 34  # レーン見出しの高さ(同上)
LINE_H = 20  # 1行の高さ
PADX, PADY = 14, 9  # ノード内側の余白
STUB = 14  # 辺から出て最初に直進する長さ
HEX_INSET = 18  # 六角形(分岐、activity図向け。Phase 14で使う想定)の左右の尖り幅


def compute_lane_geometry(state: LayoutState) -> None:
    """レーンのx位置・幅(`state.lane_x`)と図全体の幅(`state.width`)を計算する。
    全レーンを均等幅として扱う(レーンごとの幅の重みに相当する入力が無いため)。
    """
    n = len(state.lane_labels)
    avail = MAX_W - 2 * MARGIN
    base = min(320.0, avail / n)
    state.lane_x = []
    x = float(MARGIN)
    for _ in range(n):
        state.lane_x.append((x, base))
        x += base
    state.width = x + MARGIN
    _ensure_width_within_limit(state.id, state.width)


def _ensure_width_within_limit(diagram_id: str, width: float) -> None:
    """幅960pxの上限チェック(`assert`ではなく4xxへ変換可能な専用例外にする)。
    現在の均等割りレーン幅の計算式では理論上`width`は常に`MAX_W`以下になるが
    (`n * min(320, avail/n) + 2*MARGIN <= avail + 2*MARGIN == MAX_W`)、将来レーンごとの
    幅ヒントを追加する等でこの不変条件が崩れた場合に備え、独立した関数として残す。"""
    if width > MAX_W + 0.5:
        raise LayoutWidthExceededError(
            f"{diagram_id}: 幅 {width:.0f} が上限 {MAX_W} を超えています"
        )


def size_nodes(state: LayoutState) -> None:
    """各ノードの表示テキスト(折り返し済み`lines`)とサイズ(`w`/`h`)を計算する。"""
    for nd in state.nodes.values():
        lx, lw = state.lane_x[nd.lane]
        hex_pad = 2 * HEX_INSET if nd.kind == "dec" else 0
        if nd.kind == "table":
            nd.lines = nd.text.split("\n")
            widest = max(tw(line, 14) for line in nd.lines)
            nd.w = min(lw - 24, max(150, widest + 2 * PADX))
            nd.h = LINE_H * len(nd.lines) + 2 * PADY + 6
            continue
        maxw = (nd.w_hint or (lw - 40)) - 2 * PADX - hex_pad
        hard = (lw - 12) - 2 * PADX - hex_pad
        nd.lines = wrap(nd.text, maxw, hard=hard)
        widest = max(tw(line) for line in nd.lines)
        w = widest + 2 * PADX + hex_pad
        if nd.kind == "store":
            w += 10
        nd.w = min(nd.w_hint or (lw - 12), max(96 if nd.kind != "term" else 84, w))
        nd.h = LINE_H * len(nd.lines) + 2 * PADY + (10 if nd.kind == "store" else 0)


def place_rows(state: LayoutState, gap_extra: dict[int, int] | None = None) -> None:
    """各行の高さ・縦位置を計算し、各ノードの`x`/`y`を確定する。`gap_extra`は
    通路(gap/gutter)がスロットを複数必要とする場合に、その行の下の余白を広げるために使う
    (`finalize.py`の`_assign_slots`から渡される)。
    """
    gap_extra = gap_extra or {}
    rows = sorted({n.row for n in state.nodes.values()})
    state.R = (max(rows) + 1) if rows else 0
    state.rowh = dict.fromkeys(range(state.R), 0.0)
    for nd in state.nodes.values():
        state.rowh[nd.row] = max(state.rowh[nd.row], nd.h)
    # gap[g]: 行gの下の余白(g=-1は見出しの下、g=R-1は最下行の下)
    state.gap = {}
    for g in range(-1, state.R):
        base = 40 if g == -1 else (32 if g == state.R - 1 else 46)
        s = gap_extra.get(g, 0)
        state.gap[g] = max(base, 44 + 12 * (s - 1)) if s > 0 else base
    state.top_of_row = {}
    y = TOP + HEAD_H + state.gap[-1]
    for r in range(state.R):
        state.top_of_row[r] = y
        y += state.rowh[r] + state.gap[r]
    state.lanes_bottom = y
    state.height = y + MARGIN
    for nd in state.nodes.values():
        lx, lw = state.lane_x[nd.lane]
        nd.x = lx + (lw - nd.w) / 2
        nd.y = state.top_of_row[nd.row] + (state.rowh[nd.row] - nd.h) / 2


def gap_top(state: LayoutState, g: int) -> float:
    return TOP + HEAD_H if g == -1 else state.top_of_row[g] + state.rowh[g]


def gap_y(state: LayoutState, g: int, slot: int = 0) -> float:
    return gap_top(state, g) + 22 + 12 * slot


def gutter_x(state: LayoutState, i: int) -> float:
    if i == 0:
        return state.lane_x[0][0]
    lx, lw = state.lane_x[i - 1]
    return lx + lw


def port_center(nd: LayoutNode, side: str) -> tuple[float, float]:
    if side == "t":
        return (nd.cx, nd.y)
    if side == "b":
        return (nd.cx, nd.y + nd.h)
    if side == "l":
        return (nd.x, nd.cy)
    return (nd.x + nd.w, nd.cy)


def port_at(nd: LayoutNode, side: str, off: float) -> tuple[float, float]:
    if side in "tb":
        return (nd.cx + off, nd.y if side == "t" else nd.y + nd.h)
    if nd.kind == "dec":  # 六角形の斜辺に沿わせる
        half = nd.h / 2
        inset = HEX_INSET * min(1.0, abs(off) / half) if half else 0.0
        return ((nd.x + inset) if side == "l" else (nd.x + nd.w - inset), nd.cy + off)
    return (nd.x if side == "l" else nd.x + nd.w, nd.cy + off)
