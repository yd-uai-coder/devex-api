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


async def test_add_persists_cards_of_ai_message(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = ChatHistoryRepository(db_session)
    cards = [{"id": "c1", "point_key": "availability", "selected": None}]

    entry = await repo.add(project_id=project.id, sender="ai", message="質問", cards=cards)
    user = await repo.add(project_id=project.id, sender="user", message="答え")

    assert entry.cards == cards
    assert user.cards is None


async def test_list_for_project_keeps_order_within_one_transaction(
    db_session: AsyncSession,
) -> None:
    """1回の送信で、利用者の発言とAIの返信を同じトランザクションで足し、前のAIの行を更新しても
    (カードの選択の書き込み)、足した順に並ぶ。DBのnow()はトランザクションの開始時刻なので、
    時刻をアプリ側で入れないと同じ時刻になり、順が入れ替わりうる。"""
    project = await _create_project(db_session)
    repo = ChatHistoryRepository(db_session)
    first_ai = await repo.add(project_id=project.id, sender="ai", message="質問", cards=[])
    user = await repo.add(project_id=project.id, sender="user", message="答え")
    reply = await repo.add(project_id=project.id, sender="ai", message="次の質問")
    first_ai.cards = [{"id": "c1"}]
    await db_session.flush()

    assert user.created_at < reply.created_at
    history = await repo.list_for_project(project.id)
    assert [h.message for h in history] == ["質問", "答え", "次の質問"]
