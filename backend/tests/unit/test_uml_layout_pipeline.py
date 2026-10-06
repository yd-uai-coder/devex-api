from app.uml.layout import crossing_reduction, pipeline
from app.uml.layout.model import LayoutEdge, LayoutNode, LayoutState

# スタブ不要 ── 純粋関数のみで構成され、DB・外部依存を一切呼ばないため。


def test_route_produces_zero_crossings_for_simple_two_lane_chain() -> None:
    nodes = {
        "a": LayoutNode(id="a", lane=0, row=0, text="a", kind="proc"),
        "b": LayoutNode(id="b", lane=1, row=1, text="b", kind="proc"),
        "c": LayoutNode(id="c", lane=0, row=2, text="c", kind="proc"),
    }
    edges = [
        LayoutEdge(id="e1", a="a", b="b"),
        LayoutEdge(id="e2", a="b", b="c"),
    ]
    state = LayoutState("d1", ["L0", "L1"], nodes, edges)

    pipeline.route(state)

    assert state.report["crossings"] == 0
    assert state.report["collisions"] == 0
    assert state.width > 0 and state.height > 0
    for e in edges:
        assert len(e.pts) >= 2


def test_optimize_reduces_crossing_by_swapping_rows_within_a_lane() -> None:
    """lane0の2要素の行を入れ替えることで、X字に交差する2本の辺を解消できる配置。"""
    nodes = {
        "a0": LayoutNode(id="a0", lane=0, row=0, text="a0", kind="proc"),
        "a1": LayoutNode(id="a1", lane=0, row=1, text="a1", kind="proc"),
        "b0": LayoutNode(id="b0", lane=1, row=0, text="b0", kind="proc"),
        "b1": LayoutNode(id="b1", lane=1, row=1, text="b1", kind="proc"),
    }
    edges = [
        LayoutEdge(id="e1", a="a0", b="b1"),
        LayoutEdge(id="e2", a="a1", b="b0"),
    ]
    state = LayoutState("d1", ["L0", "L1"], nodes, edges)

    crossing_reduction.optimize(state)

    assert state.report["crossings"] == 0
