from app.uml.export import (
    MEDIA_TYPES,
    RenderDiagram,
    diagram_title,
    export_filename,
    render_content,
)


def test_diagram_title_uses_subject_or_whole() -> None:
    assert diagram_title("er", "") == "ER図(全体)"
    assert (
        diagram_title("dfd", "POST /api/v1/reservations")
        == "データフロー図: POST /api/v1/reservations"
    )


def test_export_filename_replaces_unsafe_characters() -> None:
    assert export_filename("component", "", "svg") == "component.svg"
    assert export_filename("dfd", "DF-1: POST /a", "drawio") == "dfd_DF-1_ POST _a.drawio"


def test_render_content_switches_by_format() -> None:
    empty = RenderDiagram(width=100, height=100, nodes=[], edges=[])

    assert render_content(empty, "svg", diagram_id="d", title="t").startswith("<svg")
    assert "<mxfile" in render_content(empty, "drawio", diagram_id="d", title="t")
    assert MEDIA_TYPES == {"drawio": "application/xml", "svg": "image/svg+xml"}
