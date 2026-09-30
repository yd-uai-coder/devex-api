import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.uml import (
    create_approved_diagram,
    create_project,
    create_project_with_internal_design,
)

from app.api.responses import content_disposition
from app.api.routes.uml import download_bundle, list_embeds, reflect_diagrams
from app.services.errors import DocumentNotFoundError


async def test_reflect_diagrams_returns_reflected_count(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    await create_approved_diagram(db_session, project.id)

    result = await reflect_diagrams(db_session, project)

    assert result.reflected == 1


async def test_reflect_diagrams_without_internal_design_is_not_found(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)

    with pytest.raises(DocumentNotFoundError):
        await reflect_diagrams(db_session, project)


async def test_list_embeds_returns_sync_state_and_svg(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)

    [embed] = await list_embeds(db_session, project)

    assert embed.diagram_id == diagram.id
    assert embed.notation == "component"
    assert embed.status == "approved"
    assert embed.doc_state == "reflected"
    assert embed.source_outdated is False
    assert embed.svg is not None


async def test_download_bundle_returns_zip_attachment(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    await create_approved_diagram(db_session, project.id)

    response = await download_bundle(db_session, project)

    assert response.media_type == "application/zip"
    assert response.headers["content-disposition"] == content_disposition("internal_design.zip")
    assert bytes(response.body).startswith(b"PK")
