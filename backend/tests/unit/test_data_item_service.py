import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.services.data_item_service import DataItemService
from app.services.errors import DataItemNameConflictError, DataItemNotFoundError


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="備品予約システム")
    session.add(project)
    await session.flush()
    return project


async def test_create_persists_data_item(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DataItemService(db_session)

    item = await service.create(project_id=project.id, name="予約情報", fields=[])

    assert item.id is not None
    assert item.name == "予約情報"


async def test_create_raises_conflict_on_duplicate_name(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DataItemService(db_session)
    await service.create(project_id=project.id, name="予約情報", fields=[])

    with pytest.raises(DataItemNameConflictError):
        await service.create(project_id=project.id, name="予約情報", fields=[])


async def test_update_renames_and_replaces_fields(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DataItemService(db_session)
    item = await service.create(project_id=project.id, name="予約情報", fields=[])

    updated = await service.update(
        project_id=project.id,
        item_id=item.id,
        name="予約情報(改)",
        fields=[{"name": "reservation_id", "type": "uuid", "required": True}],
    )

    assert updated.name == "予約情報(改)"
    assert updated.fields == [{"name": "reservation_id", "type": "uuid", "required": True}]


async def test_update_raises_conflict_when_renaming_to_existing_name(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    service = DataItemService(db_session)
    await service.create(project_id=project.id, name="予約情報", fields=[])
    other = await service.create(project_id=project.id, name="利用者情報", fields=[])

    with pytest.raises(DataItemNameConflictError):
        await service.update(project_id=project.id, item_id=other.id, name="予約情報", fields=[])


async def test_update_allows_keeping_same_name(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DataItemService(db_session)
    item = await service.create(project_id=project.id, name="予約情報", fields=[])

    updated = await service.update(
        project_id=project.id, item_id=item.id, name="予約情報", fields=[{"name": "id"}]
    )

    assert updated.fields == [{"name": "id"}]


async def test_delete_removes_data_item(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DataItemService(db_session)
    item = await service.create(project_id=project.id, name="予約情報", fields=[])

    await service.delete(project_id=project.id, item_id=item.id)

    assert await service.list_for_project(project.id) == []


async def test_update_raises_not_found_for_other_project(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    other_project = await _create_project(db_session)
    service = DataItemService(db_session)
    item = await service.create(project_id=project.id, name="予約情報", fields=[])

    with pytest.raises(DataItemNotFoundError):
        await service.update(project_id=other_project.id, item_id=item.id, name="x", fields=[])
