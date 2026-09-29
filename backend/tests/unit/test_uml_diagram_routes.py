import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.uml import create_diagram, get_diagram, update_diagram, validate_diagram
from app.core.errors import BadRequestError
from app.models.project import Project
from app.models.user import User
from app.schemas.uml_diagram import UmlDiagramCreate, UmlDiagramUpdate
from app.services.errors import UmlDiagramNotFoundError, UmlDiagramVersionConflictError
from app.uml.domain import ComponentSemanticModel, DfdSemanticModel


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="備品予約システム")
    session.add(project)
    await session.flush()
    return project


async def test_create_diagram_returns_draft_with_empty_model(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)

    result = await create_diagram(UmlDiagramCreate(notation="component"), db_session, project)

    assert result.status == "draft"
    assert result.version == 1
    assert isinstance(result.semantic_model, ComponentSemanticModel)


async def test_get_diagram_raises_not_found_for_other_project(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    other_project = await _create_project(db_session)
    created = await create_diagram(UmlDiagramCreate(notation="component"), db_session, project)

    with pytest.raises(UmlDiagramNotFoundError):
        await get_diagram(created.id, db_session, other_project)


async def test_update_diagram_increments_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    created = await create_diagram(UmlDiagramCreate(notation="component"), db_session, project)
    new_model = ComponentSemanticModel.model_validate(
        {"elements": [{"id": "c1", "name": "auth"}], "relations": []}
    )

    result = await update_diagram(
        created.id, UmlDiagramUpdate(version=1, semantic_model=new_model), db_session, project
    )

    assert result.version == 2
    assert len(result.semantic_model.elements) == 1


async def test_update_diagram_raises_conflict_on_stale_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    created = await create_diagram(UmlDiagramCreate(notation="component"), db_session, project)
    new_model = ComponentSemanticModel.model_validate({"elements": [], "relations": []})

    with pytest.raises(UmlDiagramVersionConflictError):
        await update_diagram(
            created.id, UmlDiagramUpdate(version=999, semantic_model=new_model), db_session, project
        )


async def test_update_diagram_raises_bad_request_on_notation_mismatch(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    created = await create_diagram(UmlDiagramCreate(notation="component"), db_session, project)
    dfd_model = DfdSemanticModel.model_validate({"elements": [], "relations": []})

    with pytest.raises(BadRequestError):
        await update_diagram(
            created.id, UmlDiagramUpdate(version=1, semantic_model=dfd_model), db_session, project
        )


async def test_validate_diagram_returns_valid_result_for_empty_model(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    created = await create_diagram(UmlDiagramCreate(notation="component"), db_session, project)

    result = await validate_diagram(created.id, db_session, project)

    assert result.is_valid
