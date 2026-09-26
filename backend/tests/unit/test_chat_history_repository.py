from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository


async def _create_project(session: AsyncSession) -> Project:
    user = User(email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    return project


async def test_add_persists_sender_and_message(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = ChatHistoryRepository(db_session)

    entry = await repo.add(project_id=project.id, sender="intake", message="概要: 備品予約")

    assert entry.sender == "intake"
    assert entry.message == "概要: 備品予約"


async def test_list_for_project_orders_by_created_at_asc(db_session: AsyncSession) -> None:
    # created_atをテスト内で明示的に設定する ── SQLiteのCURRENT_TIMESTAMPは秒精度のため、
    # 連続insertではタイムスタンプが同値になり得る(順序アサーションが不安定になる)のを避けるため。
    project = await _create_project(db_session)
    repo = ChatHistoryRepository(db_session)
    first = await repo.add(project_id=project.id, sender="intake", message="1つ目")
    second = await repo.add(project_id=project.id, sender="user", message="2つ目")
    first.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    second.created_at = datetime(2024, 1, 2, tzinfo=UTC)
    await db_session.flush()

    result = await repo.list_for_project(project.id)

    assert [entry.message for entry in result] == ["1つ目", "2つ目"]
