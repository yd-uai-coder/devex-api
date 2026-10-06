import xml.etree.ElementTree as ET

from app.uml.domain import ComponentElement, ComponentRelation, ComponentSemanticModel
from app.uml.export import build_render, to_svg
from app.uml.layout import compute_layout

# スタブ不要 ── to_svg は中間表現を文字列に書き出す純粋関数で、外部依存を呼ばないため。

_SVG = "{http://www.w3.org/2000/svg}"


def _render(names: tuple[str, str] = ("認証API", "認証サービス"), label: str = ""):
    model = ComponentSemanticModel(
        elements=[
            ComponentElement(id="c1", name=names[0], layer="API層"),
            ComponentElement(id="c2", name=names[1], layer="Service層"),
        ],
        relations=[ComponentRelation(id="r1", source_id="c1", target_id="c2")],
    )
    labels = {"r1": label} if label else {}
    return build_render(model, compute_layout("d1", model, labels), labels)


def test_to_svg_is_well_formed_and_draws_every_node_and_edge() -> None:
    root = ET.fromstring(to_svg(_render()))

    texts = [t.text for t in root.iter(f"{_SVG}text")]
    assert "認証API" in texts
    assert "認証サービス" in texts
    paths = [p for p in root.iter(f"{_SVG}path") if p.get("marker-end") == "url(#ar)"]
    assert len(paths) == 1


def test_to_svg_escapes_text() -> None:
    svg = to_svg(_render(names=('<script>&"', "b"), label="a<b"))

    root = ET.fromstring(svg)  # エスケープされていなければここで壊れる
    texts = [t.text for t in root.iter(f"{_SVG}text")]
    assert '<script>&"' in texts
    assert "a<b" in texts
    assert "<script>" not in svg


def test_to_svg_is_deterministic() -> None:
    assert to_svg(_render()) == to_svg(_render())
