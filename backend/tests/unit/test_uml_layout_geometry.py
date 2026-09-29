import pytest

from app.services.errors import LayoutWidthExceededError
from app.uml.layout.geometry import (
    _ensure_width_within_limit,
    compute_lane_geometry,
    place_rows,
    port_at,
    port_center,
    size_nodes,
)
from app.uml.layout.model import LayoutNode, LayoutState

# スタブ不要 ── 純粋関数のみで構成され、DB・外部依存を一切呼ばないため。


def _state(lane_labels, nodes: dict[str, LayoutNode]) -> LayoutState:
    return LayoutState("d1", lane_labels, nodes, [])


def test_compute_lane_geometry_splits_width_equally() -> None:
    state = _state(["L1", "L2"], {})

    compute_lane_geometry(state)

    assert len(state.lane_x) == 2
    (x0, w0), (x1, w1) = state.lane_x
    assert w0 == pytest.approx(w1)
    assert x1 == pytest.approx(x0 + w0)
    assert state.width <= 960 + 0.5


def test_compute_lane_geometry_keeps_width_within_limit_for_many_lanes() -> None:
    # 均等割りレーン幅の計算式は、レーン数によらずwidthが960pxを超えない不変条件を持つ
    # (n * min(320, avail/n) + 2*MARGIN <= avail + 2*MARGIN == MAX_W)。
    state = _state([f"L{i}" for i in range(50)], {})

    compute_lane_geometry(state)

    assert state.width <= 960 + 0.5


def test_ensure_width_within_limit_raises_when_exceeded() -> None:
    # 幅超過の例外化自体を、独立した関数として直接検証する。
    # 現在の均等割り計算式では実質到達しないため(上のテスト参照)、ガード関数を直接呼ぶ。
    with pytest.raises(LayoutWidthExceededError):
        _ensure_width_within_limit("d1", 1000.0)

    _ensure_width_within_limit("d1", 960.0)  # 上限ちょうどは例外にならない


def test_size_nodes_wraps_proc_text_within_lane_width() -> None:
    node = LayoutNode(
        id="a", lane=0, row=0, text="とても長い説明文をここに書いてみるテスト", kind="proc"
    )
    state = _state([None], {"a": node})
    compute_lane_geometry(state)

    size_nodes(state)

    assert node.w > 0
    assert node.h > 0
    assert len(node.lines) >= 1


def test_size_nodes_table_uses_raw_lines_without_wrapping() -> None:
    node = LayoutNode(
        id="users", lane=0, row=0, text="users\nid: uuid\nemail: string", kind="table"
    )
    state = _state([None], {"users": node})
    compute_lane_geometry(state)

    size_nodes(state)

    assert node.lines == ["users", "id: uuid", "email: string"]


def test_place_rows_positions_nodes_top_to_bottom() -> None:
    a = LayoutNode(id="a", lane=0, row=0, text="a", kind="proc")
    b = LayoutNode(id="b", lane=0, row=1, text="b", kind="proc")
    state = _state([None], {"a": a, "b": b})
    compute_lane_geometry(state)
    size_nodes(state)

    place_rows(state)

    assert state.R == 2
    assert a.y < b.y
    assert state.height > 0


def test_port_center_and_port_at_for_each_side() -> None:
    node = LayoutNode(id="a", lane=0, row=0, text="a", kind="proc", x=10, y=20, w=100, h=40)

    assert port_center(node, "t") == (60, 20)
    assert port_center(node, "b") == (60, 60)
    assert port_center(node, "l") == (10, 40)
    assert port_center(node, "r") == (110, 40)
    # top/bottomの出入口オフセットはx方向にずれる
    assert port_at(node, "t", 5) == (65, 20)
    # left/rightの出入口オフセットはy方向にずれる(proc kindはHEX_INSET補正が無い)
    assert port_at(node, "l", 5) == (10, 45)
