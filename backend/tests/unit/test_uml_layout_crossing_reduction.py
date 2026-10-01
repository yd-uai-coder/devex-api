"""交差削減の近似の評価と、時間の上限(Phase 15、気づき#10)のテスト。

SUT: crossing_reduction(sketch_score / quick_route_score / optimize)
ドライバ: 各テスト関数
スタブ不要 ── 純粋な計算のみで、DB・外部依存を呼ばないため(時間の上限は定数を差し替えて確かめる)。
"""

import time

import pytest

from app.uml.layout import crossing_reduction, pipeline
from app.uml.layout.model import LayoutEdge, LayoutNode, LayoutState


def _x_shape() -> LayoutState:
    """lane0の2要素とlane1の2要素を、X字に交差する2本の辺でつないだ配置。"""
    nodes = {
        "a0": LayoutNode(id="a0", lane=0, row=0, text="a0"),
        "a1": LayoutNode(id="a1", lane=0, row=1, text="a1"),
        "b0": LayoutNode(id="b0", lane=1, row=0, text="b0"),
        "b1": LayoutNode(id="b1", lane=1, row=1, text="b1"),
    }
    edges = [LayoutEdge(id="e1", a="a0", b="b1"), LayoutEdge(id="e2", a="a1", b="b0")]
    return LayoutState("d1", ["L0", "L1"], nodes, edges)


def test_sketch_score_counts_center_line_crossings() -> None:
    state = _x_shape()

    assert crossing_reduction.sketch_score(state) == 1
    state.nodes["a0"].row, state.nodes["a1"].row = 1, 0
    assert crossing_reduction.sketch_score(state) == 0


def test_sketch_score_penalizes_edge_through_another_node() -> None:
    """同じレーンで1つ飛ばした2要素を結ぶ線分は、間の要素を貫く。"""
    nodes = {k: LayoutNode(id=k, lane=0, row=i, text=k) for i, k in enumerate(["a", "b", "c"])}
    state = LayoutState("d1", ["L0"], nodes, [LayoutEdge(id="e1", a="a", b="c")])

    assert crossing_reduction.sketch_score(state) == 2


def test_quick_route_score_agrees_with_full_route_on_crossings() -> None:
    """簡易な評価(経路探索1巡・仕上げ無し)の交差数が、経路探索の全体と一致する小さな例。"""
    state = _x_shape()
    quick = crossing_reduction.quick_route_score(state)

    pipeline.route(state)

    assert quick // 1000 == state.report["crossings"]


def test_optimize_stays_within_time_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """時間の上限が0でも、最後の経路探索まで終えて結果を返す(安全弁が効いていること)。"""
    monkeypatch.setattr(crossing_reduction, "SKETCH_BUDGET_SECONDS", 0.0)
    monkeypatch.setattr(crossing_reduction, "REFINE_BUDGET_SECONDS", 0.0)
    state = _x_shape()

    started = time.monotonic()
    crossing_reduction.optimize(state)

    assert time.monotonic() - started < 5
    assert "crossings" in state.report
