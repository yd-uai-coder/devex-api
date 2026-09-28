import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_template import PromptTemplate
from app.models.user import User
from app.repositories.project import ProjectRepository


async def _create_user(session: AsyncSession) -> User:
    user = User(email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    return user


async def test_create_persists_intake(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    repo = ProjectRepository(db_session)

    project = await repo.create(
        user_id=user.id,
        title="備品予約システム",
        intake={"system_overview": "備品予約を一元管理したい"},
    )

    assert project.status == "interviewing"
    assert project.intake == {"system_overview": "備品予約を一元管理したい"}


async def test_get_by_id_returns_none_for_other_users_project(db_session: AsyncSession) -> None:
    owner = await _create_user(db_session)
    repo = ProjectRepository(db_session)
    project = await repo.create(user_id=owner.id, title="p")

    result = await repo.get_by_id(project.id, user_id=uuid.uuid4())

    assert result is None


async def test_list_for_user_orders_by_updated_at_desc(db_session: AsyncSession) -> None:
    # updated_atをテスト内で明示的に設定する ── SQLiteのCURRENT_TIMESTAMPは秒精度のため、
    # 連続insertではタイムスタンプが同値になり得る(順序アサーションが不安定になる)のを避けるため。
    user = await _create_user(db_session)
    repo = ProjectRepository(db_session)
    first = await repo.create(user_id=user.id, title="first")
    second = await repo.create(user_id=user.id, title="second")
    first.updated_at = datetime(2024, 1, 1, tzinfo=UTC)
    second.updated_at = datetime(2024, 1, 2, tzinfo=UTC)
    await db_session.flush()

    result = await repo.list_for_user(user.id)

    assert [p.id for p in result] == [second.id, first.id]

async def test_create_persists_template_id(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    template = PromptTemplate(name="Webアプリケーション標準", target_type="Web", system_prompt="x")
    db_session.add(template)
    await db_session.flush()
    repo = ProjectRepository(db_session)

    project = await repo.create(user_id=user.id, title="p", template_id=template.id)

    assert project.template_id == template.id


async def test_create_defaults_template_id_to_none(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)
    repo = ProjectRepository(db_session)

    project = await repo.create(user_id=user.id, title="p")

    assert project.template_id is None
