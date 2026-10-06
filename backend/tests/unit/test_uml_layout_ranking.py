from app.uml.domain import (
    ComponentElement,
    ComponentRelation,
    ErColumn,
    ErElement,
    ErRelation,
)
from app.uml.layout.ranking import assign_lanes_and_rows

# スタブ不要 ── 純粋関数のみで構成され、DB・外部依存を一切呼ばないため。


def test_assign_rows_linear_chain_increases_by_one() -> None:
    elements = [
        ComponentElement(id="a", name="a"),
        ComponentElement(id="b", name="b"),
        ComponentElement(id="c", name="c"),
    ]
    relations = [
        ComponentRelation(id="r1", source_id="a", target_id="b"),
        ComponentRelation(id="r2", source_id="b", target_id="c"),
    ]

    _labels, _lanes, rows = assign_lanes_and_rows("component", elements, relations)

    assert rows == {"a": 0, "b": 1, "c": 2}


def test_assign_rows_diamond_shape_takes_longest_path() -> None:
    elements = [
        ComponentElement(id="a", name="a"),
        ComponentElement(id="b", name="b"),
        ComponentElement(id="c", name="c"),
        ComponentElement(id="d", name="d"),
    ]
    relations = [
        ComponentRelation(id="r1", source_id="a", target_id="b"),
        ComponentRelation(id="r2", source_id="a", target_id="c"),
        ComponentRelation(id="r3", source_id="b", target_id="d"),
        ComponentRelation(id="r4", source_id="c", target_id="d"),
    ]

    _labels, _lanes, rows = assign_lanes_and_rows("component", elements, relations)

    # b・c は同じレーン(layer 未設定)なので同じ行には置かない(同じ (lane, row) は重なる)。
    # 定義順で b が先、c は次の空き行へ下がり、d は両方より下に来る。
    assert rows == {"a": 0, "b": 1, "c": 2, "d": 3}


def test_assign_rows_same_lane_sources_do_not_share_a_row() -> None:
    """前提の無い要素が同じレーンに2つあっても、同じ行にしない(デモで重なった形)。"""
    elements = [
        ComponentElement(id="auth", name="認証ルート", layer="API層"),
        ComponentElement(id="project", name="プロジェクトルート", layer="API層"),
        ComponentElement(id="svc", name="認証サービス", layer="Service層"),
    ]
    relations = [ComponentRelation(id="r1", source_id="auth", target_id="svc")]

    _labels, lanes, rows = assign_lanes_and_rows("component", elements, relations)

    assert rows == {"auth": 0, "project": 1, "svc": 1}
    assert len({(lanes[i], rows[i]) for i in rows}) == len(rows)


def test_assign_rows_different_lanes_may_share_a_row() -> None:
    """別レーンなら同じ行を共有してよい(図を不必要に縦へ伸ばさない)。"""
    elements = [
        ComponentElement(id="a", name="a", layer="L1"),
        ComponentElement(id="b", name="b", layer="L2"),
    ]

    _labels, _lanes, rows = assign_lanes_and_rows("component", elements, [])

    assert rows == {"a": 0, "b": 0}


def test_assign_rows_is_deterministic_by_definition_order() -> None:
    """同じ深さで衝突したときは、定義順で先の要素が上の行になる。"""
    elements = [
        ComponentElement(id="z", name="z"),
        ComponentElement(id="a", name="a"),
    ]

    _labels, _lanes, rows = assign_lanes_and_rows("component", elements, [])

    assert rows == {"z": 0, "a": 1}


def test_assign_rows_handles_cycle_without_raising() -> None:
    """a->b->c->aという循環を含むグラフでも、DFSのback edge除外により層分けが完了する。"""
    elements = [
        ComponentElement(id="a", name="a"),
        ComponentElement(id="b", name="b"),
        ComponentElement(id="c", name="c"),
    ]
    relations = [
        ComponentRelation(id="r1", source_id="a", target_id="b"),
        ComponentRelation(id="r2", source_id="b", target_id="c"),
        ComponentRelation(id="r3", source_id="c", target_id="a"),
    ]

    _labels, _lanes, rows = assign_lanes_and_rows("component", elements, relations)

    assert rows == {"a": 0, "b": 1, "c": 2}


def test_assign_rows_isolated_node_gets_row_zero() -> None:
    elements = [ComponentElement(id="a", name="a")]

    _labels, _lanes, rows = assign_lanes_and_rows("component", elements, [])

    assert rows == {"a": 0}


def test_assign_lanes_orders_by_first_appearance_and_groups_unlabeled() -> None:
    elements = [
        ComponentElement(id="a", name="a", layer="API層"),
        ComponentElement(id="b", name="b", layer="Service層"),
        ComponentElement(id="c", name="c"),  # layer未設定
        ComponentElement(id="d", name="d", layer="API層"),
    ]

    labels, lanes, _rows = assign_lanes_and_rows("component", elements, [])

    assert labels == ["API層", "Service層", None]
    assert lanes == {"a": 0, "b": 1, "c": 2, "d": 0}


def test_assign_lanes_all_labeled_has_no_fallback_lane() -> None:
    elements = [
        ComponentElement(id="a", name="a", layer="L1"),
        ComponentElement(id="b", name="b", layer="L2"),
    ]

    labels, _lanes, _rows = assign_lanes_and_rows("component", elements, [])

    assert labels == ["L1", "L2"]


def test_er_uses_single_lane_and_definition_order_row() -> None:
    elements = [
        ErElement(id="users", name="users", columns=[ErColumn(name="id", type="uuid")]),
        ErElement(id="orders", name="orders", columns=[ErColumn(name="id", type="uuid")]),
    ]
    relations = [
        ErRelation(id="r1", source_id="orders", target_id="users", relation_type="many_to_many")
    ]

    labels, lanes, rows = assign_lanes_and_rows("er", elements, relations)

    assert labels == [None]
    assert lanes == {"users": 0, "orders": 0}
    # 定義順(elementsのインデックス)がそのままrowになる(関係の向きに関わらず)
    assert rows == {"users": 0, "orders": 1}
