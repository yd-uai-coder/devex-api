import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_project
from app.models.project import Project
from app.models.user import User
from app.services.errors import ProjectNotFoundError


async def _create_user(session: AsyncSession) -> User:
    user = User(email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    return user


async def test_returns_project_for_owner(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session)
    project = Project(user_id=owner.id, title="p")
    db_session.add(project)
    await db_session.flush()

    result = await get_current_project(project.id, db_session, owner)

    assert result.id == project.id


async def test_raises_not_found_for_non_owner(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session)
    project = Project(user_id=owner.id, title="p")
    db_session.add(project)
    await db_session.flush()
    other_user = User(email="other@example.com", hashed_password="x")
    db_session.add(other_user)
    await db_session.flush()

    with pytest.raises(ProjectNotFoundError):
        await get_current_project(project.id, db_session, other_user)


async def test_raises_not_found_for_missing_project(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session)

    with pytest.raises(ProjectNotFoundError):
        await get_current_project(uuid.uuid4(), db_session, owner)
