
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.uml import (
    create_empty_diagram,
    create_project,
)

from app.api.responses import content_disposition
from app.api.routes.uml import (
    approve_diagram,
    compute_diagram_layout,
    export_diagram_drawio,
    export_diagram_svg,
    get_diagram,
    list_diagrams,
    update_diagram,
    validate_diagram,
)
from app.core.errors import BadRequestError
from app.schemas.uml_diagram import UmlDiagramApprove, UmlDiagramUpdate
from app.services.errors import (
    UmlDiagramNotApprovedError,
    UmlDiagramNotFoundError,
    UmlDiagramVersionConflictError,
    UmlLayoutRequiredError,
)
from app.uml.domain import ComponentSemanticModel, DfdSemanticModel
from app.uml.layout import LayoutModel


async def test_get_diagram_raises_not_found_for_other_project(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    other_project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")

    with pytest.raises(UmlDiagramNotFoundError):
        await get_diagram(created.id, db_session, other_project)


async def test_update_diagram_increments_version(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    new_model = ComponentSemanticModel.model_validate(
        {"elements": [{"id": "c1", "name": "auth"}], "relations": []}
    )

    result = await update_diagram(
        created.id, UmlDiagramUpdate(version=1, semantic_model=new_model), db_session, project
    )

    assert result.version == 2
    assert len(result.semantic_model.elements) == 1


async def test_update_diagram_passes_layout_model_to_service(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    new_model = ComponentSemanticModel.model_validate(
        {"elements": [{"id": "c1", "name": "auth"}], "relations": []}
    )
    layout = LayoutModel.model_validate(
        {
            "width": 200,
            "height": 100,
            "nodes": {"c1": {"x": 40, "y": 20, "w": 100, "h": 40, "lane": 0, "row": 0}},
            "edges": {},
            "metrics": {"crossings": 0, "overlaps": 0, "collisions": 0},
        }
    )

    result = await update_diagram(
        created.id,
        UmlDiagramUpdate(version=1, semantic_model=new_model, layout_model=layout),
        db_session,
        project,
    )

    assert result.version == 2
    assert result.layout_model is not None
    assert result.layout_model.nodes["c1"].x == 40


async def test_update_diagram_raises_conflict_on_stale_version(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    new_model = ComponentSemanticModel.model_validate({"elements": [], "relations": []})

    with pytest.raises(UmlDiagramVersionConflictError):
        await update_diagram(
            created.id, UmlDiagramUpdate(version=999, semantic_model=new_model), db_session, project
        )


async def test_update_diagram_raises_bad_request_on_notation_mismatch(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    dfd_model = DfdSemanticModel.model_validate({"elements": [], "relations": []})

    with pytest.raises(BadRequestError):
        await update_diagram(
            created.id, UmlDiagramUpdate(version=1, semantic_model=dfd_model), db_session, project
        )


async def test_validate_diagram_returns_valid_result_for_empty_model(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")

    result = await validate_diagram(created.id, db_session, project)

    assert result.is_valid


async def test_compute_diagram_layout_returns_layout_model(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [{"id": "c1", "name": "a"}, {"id": "c2", "name": "b"}],
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
        }
    )
    await update_diagram(
        created.id, UmlDiagramUpdate(version=1, semantic_model=model), db_session, project
    )

    result = await compute_diagram_layout(created.id, db_session, project)

    assert result.layout_model is not None
    assert set(result.layout_model.nodes) == {"c1", "c2"}


async def test_list_diagrams_returns_project_diagrams(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    await create_empty_diagram(db_session, project.id, "component")

    diagrams = await list_diagrams(db_session, project)

    assert [(d.notation, d.generation_status) for d in diagrams] == [("component", "completed")]


# ---- 承認
async def test_approve_diagram_returns_approved_diagram(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [{"id": "c1", "name": "a"}, {"id": "c2", "name": "b"}],
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
        }
    )
    await update_diagram(
        created.id, UmlDiagramUpdate(version=1, semantic_model=model), db_session, project
    )
    await compute_diagram_layout(created.id, db_session, project)

    result = await approve_diagram(created.id, UmlDiagramApprove(version=2), db_session, project)

    assert result.status == "approved"
    assert result.version == 2


async def test_approve_diagram_without_layout_is_bad_request(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")

    with pytest.raises(UmlLayoutRequiredError):
        await approve_diagram(created.id, UmlDiagramApprove(version=1), db_session, project)


# ---- 出力
async def _approved_diagram_id(db_session: AsyncSession, project) -> uuid.UUID:
    created = await create_empty_diagram(db_session, project.id, "component")
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [{"id": "c1", "name": "認証API"}, {"id": "c2", "name": "認証サービス"}],
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
        }
    )
    await update_diagram(
        created.id, UmlDiagramUpdate(version=1, semantic_model=model), db_session, project
    )
    await compute_diagram_layout(created.id, db_session, project)
    await approve_diagram(created.id, UmlDiagramApprove(version=2), db_session, project)
    return created.id


async def test_export_diagram_drawio_returns_attachment(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    diagram_id = await _approved_diagram_id(db_session, project)

    response = await export_diagram_drawio(diagram_id, db_session, project)

    assert response.media_type == "application/xml"
    assert response.headers["Content-Disposition"] == content_disposition("component.drawio")
    assert b"<mxfile" in response.body


async def test_export_diagram_svg_returns_svg(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    diagram_id = await _approved_diagram_id(db_session, project)

    response = await export_diagram_svg(diagram_id, db_session, project)

    assert response.media_type == "image/svg+xml"
    assert bytes(response.body).startswith(b"<svg")


async def test_export_diagram_rejects_unapproved_diagram(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")

    with pytest.raises(UmlDiagramNotApprovedError):
        await export_diagram_svg(created.id, db_session, project)
