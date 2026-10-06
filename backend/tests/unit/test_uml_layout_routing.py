import pytest

from app.services.errors import LayoutRouteNotFoundError
from app.uml.layout import routing
from app.uml.layout.geometry import compute_lane_geometry, place_rows, size_nodes
from app.uml.layout.model import LayoutEdge, LayoutNode, LayoutState

# スタブ不要 ── 純粋関数のみで構成され、DB・外部依存を一切呼ばないため。


def _routed_state(
    lane_labels, nodes: dict[str, LayoutNode], edges: list[LayoutEdge]
) -> LayoutState:
    state = LayoutState("d1", lane_labels, nodes, edges)
    compute_lane_geometry(state)
    size_nodes(state)
    place_rows(state)
    return state


def test_route_edges_connects_adjacent_rows_with_few_bends() -> None:
    a = LayoutNode(id="a", lane=0, row=0, text="a", kind="proc")
    b = LayoutNode(id="b", lane=0, row=1, text="b", kind="proc")
    edge = LayoutEdge(id="e1", a="a", b="b")
    state = _routed_state([None], {"a": a, "b": b}, [edge])

    routing.route_edges(state)

    assert edge.pts[0][1] == pytest.approx(a.y + a.h)  # aの下端から出る
    assert edge.pts[-1][1] == pytest.approx(b.y)  # bの上端へ入る
    assert len(edge.pts) <= 4  # 直行できる配置なので折れは少ない


def test_route_edges_avoids_crossing_between_two_independent_edges() -> None:
    a = LayoutNode(id="a", lane=0, row=0, text="a", kind="proc")
    b = LayoutNode(id="b", lane=0, row=1, text="b", kind="proc")
    c = LayoutNode(id="c", lane=1, row=0, text="c", kind="proc")
    d = LayoutNode(id="d", lane=1, row=1, text="d", kind="proc")
    e1 = LayoutEdge(id="e1", a="a", b="b")
    e2 = LayoutEdge(id="e2", a="c", b="d")
    state = _routed_state([None, None], {"a": a, "b": b, "c": c, "d": d}, [e1, e2])

    routing.route_edges(state)

    from app.uml.layout.model import seg_cross

    crossed = any(
        seg_cross(e1.pts[i], e1.pts[i + 1], e2.pts[j], e2.pts[j + 1])
        for i in range(len(e1.pts) - 1)
        for j in range(len(e2.pts) - 1)
    )
    assert not crossed


def test_route_edges_raises_when_no_candidate_found(monkeypatch: pytest.MonkeyPatch) -> None:
    a = LayoutNode(id="a", lane=0, row=0, text="a", kind="proc")
    b = LayoutNode(id="b", lane=0, row=1, text="b", kind="proc")
    edge = LayoutEdge(id="e1", a="a", b="b")
    state = _routed_state([None], {"a": a, "b": b}, [edge])

    def _no_candidates(_state, _edge, _offs):
        return iter(())

    monkeypatch.setattr(routing, "_candidates", _no_candidates)

    with pytest.raises(LayoutRouteNotFoundError):
        routing.route_edges(state)
