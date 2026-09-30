import pytest

from app.services.errors import UmlLayoutRequiredError
from app.uml.domain import (
    ComponentElement,
    ComponentRelation,
    ComponentSemanticModel,
    ErColumn,
    ErElement,
    ErRelation,
    ErSemanticModel,
)
from app.uml.export import build_render, orthogonal_fallback
from app.uml.export.render import path_midpoint
from app.uml.layout import LayoutModel, compute_layout, edge_labels
from app.uml.layout.model import LayoutBox

# スタブ不要 ── build_render は意味モデル・配置・ラベルを受け取って中間表現を返す純粋関数で、
# DB・外部依存を呼ばないため(配置は純粋なcompute_layoutで作るか、手で組み立てる)。


def _box(x: float, y: float, w: float = 100, h: float = 40) -> LayoutBox:
    return LayoutBox(x=x, y=y, w=w, h=h, lane=0, row=0)


def _two_modules() -> ComponentSemanticModel:
    return ComponentSemanticModel(
        elements=[
            ComponentElement(id="c1", name="認証API", layer="API層"),
            ComponentElement(id="c2", name="認証サービス", layer="Service層"),
        ],
        relations=[ComponentRelation(id="r1", source_id="c1", target_id="c2")],
    )


def test_build_render_uses_engine_route_and_rewraps_lines() -> None:
    model = _two_modules()
    layout = compute_layout("d1", model)

    render = build_render(model, layout, {})

    edge = render.edges[0]
    assert edge.routed is True
    assert edge.points == layout.edges["r1"].points
    assert edge.arrow is True
    assert [node.lines for node in render.nodes] == [["認証API"], ["認証サービス"]]


def test_build_render_splits_table_lines_and_omits_er_arrows() -> None:
    model = ErSemanticModel(
        elements=[
            ErElement(
                id="t1",
                name="users",
                columns=[ErColumn(name="id", type="uuid", is_primary_key=True)],
            ),
            ErElement(id="t2", name="reservations", columns=[]),
        ],
        relations=[
            ErRelation(id="r1", source_id="t1", target_id="t2", relation_type="one_to_many")
        ],
    )
    layout = compute_layout("d1", model, edge_labels(model))

    render = build_render(model, layout, edge_labels(model))

    assert render.nodes[0].kind == "table"
    assert render.nodes[0].lines == ["users", "PK id: uuid"]
    assert render.edges[0].arrow is False
    assert render.edges[0].label == "1:N"
    assert render.edges[0].label_pos == layout.edges["r1"].label_pos


def test_build_render_makes_fallback_route_for_moved_edges() -> None:
    model = _two_modules()
    layout = LayoutModel.model_validate(
        {
            "width": 400,
            "height": 200,
            "nodes": {
                "c1": {"x": 10, "y": 10, "w": 100, "h": 40, "lane": 0, "row": 0},
                "c2": {"x": 250, "y": 120, "w": 100, "h": 40, "lane": 0, "row": 0},
            },
            "edges": {"r1": {"points": [], "label_pos": None}},
            "metrics": {"crossings": 0, "overlaps": 0, "collisions": 0},
        }
    )

    edge = build_render(model, layout, {}).edges[0]

    assert edge.routed is False
    assert edge.points == [(110.0, 30.0), (180.0, 30.0), (180.0, 140.0), (250.0, 140.0)]


def test_build_render_rejects_elements_without_layout() -> None:
    model = _two_modules()
    layout = compute_layout("d1", model)
    del layout.nodes["c2"]

    with pytest.raises(UmlLayoutRequiredError, match="c2"):
        build_render(model, layout, {})


def test_orthogonal_fallback_goes_vertically_when_boxes_are_stacked() -> None:
    points = orthogonal_fallback(_box(10, 10), _box(10, 200))

    # 同じ列なので一直線(中間の折れ点は取り除かれる)
    assert points == [(60.0, 50.0), (60.0, 200.0)]


def test_path_midpoint_is_half_of_the_path_length() -> None:
    assert path_midpoint([(0, 0), (100, 0), (100, 100)]) == (100.0, 0.0)
    assert path_midpoint([(0, 0), (0, 40)]) == (0.0, 20.0)
