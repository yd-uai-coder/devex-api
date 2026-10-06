from app.uml.domain import ComponentSemanticModel
from app.uml.layout import LayoutModel, reconcile_layout


def _layout() -> LayoutModel:
    return LayoutModel.model_validate(
        {
            "width": 400,
            "height": 200,
            "nodes": {
                "c1": {"x": 10, "y": 10, "w": 100, "h": 40, "lane": 0, "row": 0},
                "c2": {"x": 200, "y": 10, "w": 100, "h": 40, "lane": 1, "row": 0},
            },
            "edges": {"r1": {"points": [[110, 30], [200, 30]]}},
            "metrics": {"crossings": 0, "overlaps": 0, "collisions": 0},
        }
    )


def test_reconcile_layout_drops_geometry_of_deleted_elements_and_relations() -> None:
    model = ComponentSemanticModel.model_validate(
        {"elements": [{"id": "c1", "name": "auth"}], "relations": []}
    )

    reconciled = reconcile_layout(_layout(), model)

    assert set(reconciled.nodes) == {"c1"}
    assert reconciled.edges == {}


def test_reconcile_layout_does_not_add_boxes_for_new_elements() -> None:
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [
                {"id": "c1", "name": "auth"},
                {"id": "c2", "name": "users"},
                {"id": "c3", "name": "新規"},
            ],
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
        }
    )

    reconciled = reconcile_layout(_layout(), model)

    assert set(reconciled.nodes) == {"c1", "c2"}
    assert set(reconciled.edges) == {"r1"}


def test_reconcile_layout_widens_canvas_for_manually_moved_node() -> None:
    layout = _layout()
    layout.nodes["c2"] = layout.nodes["c2"].model_copy(update={"x": 500, "y": 300})
    layout.edges["r1"] = layout.edges["r1"].model_copy(update={"points": []})
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [{"id": "c1", "name": "auth"}, {"id": "c2", "name": "users"}],
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
        }
    )

    reconciled = reconcile_layout(layout, model)

    assert reconciled.width == 600
    assert reconciled.height == 340
    assert reconciled.edges["r1"].points == []


# ---- 折れ点を捨てた辺はラベルの位置も捨てる
def test_reconcile_layout_drops_label_pos_of_edges_without_points() -> None:
    layout = _layout()
    layout.edges["r1"] = layout.edges["r1"].model_copy(update={"label_pos": (155.0, 20.0)})
    moved = layout.model_copy(
        update={"edges": {"r1": layout.edges["r1"].model_copy(update={"points": []})}}
    )
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [{"id": "c1", "name": "auth"}, {"id": "c2", "name": "users"}],
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
        }
    )

    kept = reconcile_layout(layout, model)
    dropped = reconcile_layout(moved, model)

    assert kept.edges["r1"].label_pos == (155.0, 20.0)
    assert dropped.edges["r1"].label_pos is None
