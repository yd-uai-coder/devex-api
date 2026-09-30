import xml.etree.ElementTree as ET

from app.uml.domain import ComponentElement, ComponentRelation, ComponentSemanticModel
from app.uml.export import build_render, to_drawio
from app.uml.layout import LayoutModel, compute_layout

# スタブ不要 ── to_drawio は中間表現を文字列に書き出す純粋関数で、外部依存を呼ばないため。


def _model(first_name: str = "認証API") -> ComponentSemanticModel:
    return ComponentSemanticModel(
        elements=[
            ComponentElement(id="c1", name=first_name, layer="API層"),
            ComponentElement(id="c2", name="認証サービス", layer="Service層"),
        ],
        relations=[ComponentRelation(id="r1", source_id="c1", target_id="c2")],
    )


def _cells(xml: str) -> dict[str, ET.Element]:
    root = ET.fromstring(xml)
    return {cell.get("id", ""): cell for cell in root.iter("mxCell")}


def test_to_drawio_writes_nodes_and_routed_edge() -> None:
    model = _model()
    render = build_render(model, compute_layout("d1", model), {})

    cells = _cells(to_drawio(render, diagram_id="d1", title="コンポーネント図"))

    assert cells["n-c1"].get("vertex") == "1"
    assert cells["n-c1"].get("value") == "認証API"
    edge = cells["e-r1"]
    assert edge.get("source") == "n-c1"
    assert edge.get("target") == "n-c2"
    style = edge.get("style", "")
    assert "edgeStyle=orthogonalEdgeStyle" in style
    assert "exitX=" in style
    assert "endArrow=block" in style


def test_to_drawio_leaves_moved_edges_to_orthogonal_edge_style() -> None:
    """端点のノードを手で動かした辺(points=[])は、出入口も折れ点も書かない(D2)。"""
    model = _model()
    layout = LayoutModel.model_validate(
        {
            "width": 400,
            "height": 200,
            "nodes": {
                "c1": {"x": 10, "y": 10, "w": 100, "h": 40, "lane": 0, "row": 0},
                "c2": {"x": 250, "y": 120, "w": 100, "h": 40, "lane": 0, "row": 0},
            },
            "edges": {"r1": {"points": []}},
            "metrics": {"crossings": 0, "overlaps": 0, "collisions": 0},
        }
    )

    edge = _cells(to_drawio(build_render(model, layout, {}), diagram_id="d1", title="t"))["e-r1"]

    assert "exitX" not in edge.get("style", "")
    assert edge.find("mxGeometry/Array") is None


def test_to_drawio_escapes_values_twice_for_html_labels() -> None:
    """draw.ioの`html=1`のセルは値をHTMLとして解釈するため、HTMLとしてエスケープした上で
    XML属性としてもエスケープする(属性を読み戻すとHTMLエスケープ済みの文字列になる)。"""
    model = _model(first_name='<b>&"')
    render = build_render(model, compute_layout("d1", model), {})

    xml = to_drawio(render, diagram_id="d1", title='図<1>&"')

    cells = _cells(xml)
    assert cells["n-c1"].get("value") == "&lt;b&gt;&amp;&quot;"
    diagram = ET.fromstring(xml).find("diagram")
    assert diagram is not None
    assert diagram.get("name") == '図<1>&"'


def test_to_drawio_is_deterministic() -> None:
    model = _model()
    render = build_render(model, compute_layout("d1", model), {})

    assert to_drawio(render, diagram_id="d1", title="t") == to_drawio(
        render, diagram_id="d1", title="t"
    )
