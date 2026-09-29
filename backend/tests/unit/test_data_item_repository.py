import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.repositories.data_item import DataItemRepository


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="備品予約システム")
    session.add(project)
    await session.flush()
    return project


async def test_create_persists_fields(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = DataItemRepository(db_session)

    item = await repo.create(
        project_id=project.id,
        name="予約情報",
        fields=[{"name": "reservation_id", "type": "uuid", "required": True}],
    )

    assert item.id is not None
    assert item.fields == [{"name": "reservation_id", "type": "uuid", "required": True}]


async def test_create_defaults_fields_to_empty_list(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = DataItemRepository(db_session)

    item = await repo.create(project_id=project.id, name="利用者情報")

    assert item.fields == []


async def test_get_by_id_returns_none_for_other_projects_item(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    other_project = await _create_project(db_session)
    repo = DataItemRepository(db_session)
    item = await repo.create(project_id=project.id, name="予約情報")

    result = await repo.get_by_id(item.id, project_id=other_project.id)

    assert result is None


async def test_list_for_project_orders_by_name(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = DataItemRepository(db_session)
    await repo.create(project_id=project.id, name="利用者情報")
    await repo.create(project_id=project.id, name="予約情報")

    result = await repo.list_for_project(project.id)

    # SQLiteのTEXT比較はUnicodeコードポイント順(「予」U+4E88 < 「利」U+5229)。
    assert [item.name for item in result] == ["予約情報", "利用者情報"]


async def test_find_by_name_returns_none_when_absent(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = DataItemRepository(db_session)

    result = await repo.find_by_name(project_id=project.id, name=str(uuid.uuid4()))

    assert result is None
