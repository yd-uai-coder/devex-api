import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.uml import create_data_item, delete_data_item, list_data_items, update_data_item
from app.models.project import Project
from app.models.user import User
from app.schemas.data_item import DataItemCreate, DataItemUpdate
from app.services.errors import DataItemNameConflictError, DataItemNotFoundError
from app.uml.domain import DataItemField


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="備品予約システム")
    session.add(project)
    await session.flush()
    return project


async def test_create_data_item_returns_created_item(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    payload = DataItemCreate(name="予約情報", fields=[DataItemField(name="reservation_id")])

    result = await create_data_item(payload, db_session, project)

    assert result.name == "予約情報"
    assert result.fields == [DataItemField(name="reservation_id")]


async def test_list_data_items_returns_project_scoped_items(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    other_project = await _create_project(db_session)
    await create_data_item(DataItemCreate(name="予約情報"), db_session, project)
    await create_data_item(DataItemCreate(name="利用者情報"), db_session, other_project)

    result = await list_data_items(db_session, project)

    assert [item.name for item in result] == ["予約情報"]


async def test_update_data_item_replaces_name_and_fields(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    created = await create_data_item(DataItemCreate(name="予約情報"), db_session, project)

    updated = await update_data_item(
        created.id,
        DataItemUpdate(name="予約情報(改)", fields=[DataItemField(name="id", type="uuid")]),
        db_session,
        project,
    )

    assert updated.name == "予約情報(改)"


async def test_update_data_item_raises_conflict_on_duplicate_name(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    await create_data_item(DataItemCreate(name="予約情報"), db_session, project)
    other = await create_data_item(DataItemCreate(name="利用者情報"), db_session, project)

    with pytest.raises(DataItemNameConflictError):
        await update_data_item(other.id, DataItemUpdate(name="予約情報"), db_session, project)


async def test_delete_data_item_removes_it(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    created = await create_data_item(DataItemCreate(name="予約情報"), db_session, project)

    await delete_data_item(created.id, db_session, project)

    assert await list_data_items(db_session, project) == []


async def test_update_data_item_raises_not_found_for_other_project(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    other_project = await _create_project(db_session)
    created = await create_data_item(DataItemCreate(name="予約情報"), db_session, project)

    with pytest.raises(DataItemNotFoundError):
        await update_data_item(
            created.id, DataItemUpdate(name="x"), db_session, other_project
        )
