from app.uml.layout import finalize, pipeline
from app.uml.layout.model import LayoutEdge, LayoutNode, LayoutState

# スタブ不要 ── 純粋関数のみで構成され、DB・外部依存を一切呼ばないため。


def test_assign_ports_groups_edges_sharing_same_node_and_side() -> None:
    nodes = {
        "hub": LayoutNode(id="hub", lane=0, row=1, text="hub", kind="proc"),
        "a": LayoutNode(id="a", lane=0, row=0, text="a", kind="proc"),
        "b": LayoutNode(id="b", lane=0, row=2, text="b", kind="proc"),
    }
    edges = [LayoutEdge(id="e1", a="a", b="hub"), LayoutEdge(id="e2", a="hub", b="b")]
    state = LayoutState("d1", [None], nodes, edges)
    pipeline.route(state)  # choice/ptsを確定させる(_assign_portsはchoiceに依存する)

    offs = finalize._assign_ports(state)

    # hubは2本の辺(e1の入り口・e2の出口)を持つが、それぞれ別の(node, side)グループになりうる
    assert any(key[0] == "hub" for key in state.port_groups)
    assert isinstance(offs, dict)


def test_offs_from_groups_single_edge_has_zero_offset() -> None:
    nodes = {
        "a": LayoutNode(id="a", lane=0, row=0, text="a", kind="proc"),
        "b": LayoutNode(id="b", lane=0, row=1, text="b", kind="proc"),
    }
    edges = [LayoutEdge(id="e1", a="a", b="b")]
    state = LayoutState("d1", [None], nodes, edges)
    pipeline.route(state)

    offs = finalize._offs_from_groups(state)

    assert all(v == 0 for v in offs.values())


def test_assign_slots_gives_distinct_slots_to_overlapping_gap_edges() -> None:
    """同じgap(行と行の間の通路)を通る辺が2本、区間が重なる場合は別スロットになる。
    `choice`を直接gapタグに設定し、区間スケジューリングのロジック自体を単独で検証する
    (`_candidates`のコスト計算はL1/L2等の他の経路を選びうるため、経路探索は経由しない)。
    """
    # 4ノードとも同じレーン(同じx幅)に置き、port位置(cx)を揃えることで
    # 2本の辺のgap上の区間(x方向)を確実に重ねる。
    nodes = {
        "a": LayoutNode(id="a", lane=0, row=0, text="a", kind="proc", x=0, y=0, w=80, h=30),
        "b": LayoutNode(id="b", lane=0, row=2, text="b", kind="proc", x=0, y=100, w=80, h=30),
        "c": LayoutNode(id="c", lane=0, row=0, text="c", kind="proc", x=0, y=0, w=80, h=30),
        "d": LayoutNode(id="d", lane=0, row=2, text="d", kind="proc", x=0, y=100, w=80, h=30),
    }
    e1 = LayoutEdge(id="e1", a="a", b="b", choice=("b", "t", "gap", 0))
    e2 = LayoutEdge(id="e2", a="c", b="d", choice=("b", "t", "gap", 0))
    state = LayoutState("d1", ["L0", "L1"], nodes, [e1, e2])
    state.R = 3
    state.top_of_row = {0: 0, 1: 50, 2: 100}
    state.rowh = {0: 30, 1: 0, 2: 30}

    offs = finalize._offs_from_groups(state)  # port_groupsが空でも{}を返す(単体呼び出し)
    slots, gap_extra = finalize.assign_slots(state, offs)

    # a→bとc→dの区間(x方向)は重なるため、同じgap(0)でも異なるスロットになる
    assert slots[id(e1)] != slots[id(e2)]
    assert gap_extra[0] == 2


def test_measure_populates_report_with_expected_keys() -> None:
    nodes = {
        "a": LayoutNode(id="a", lane=0, row=0, text="a", kind="proc"),
        "b": LayoutNode(id="b", lane=0, row=1, text="b", kind="proc"),
    }
    edges = [LayoutEdge(id="e1", a="a", b="b")]
    state = LayoutState("d1", [None], nodes, edges)
    pipeline.route(state)

    expected_keys = {"id", "w", "h", "nodes", "edges", "crossings", "overlaps", "collisions"}
    assert set(state.report) >= expected_keys
    assert state.report["nodes"] == 2
    assert state.report["edges"] == 1
