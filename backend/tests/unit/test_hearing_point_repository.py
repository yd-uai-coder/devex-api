"""観点の表の永続化のテスト。

SUT: HearingPointRepository(app/repositories/hearing_point.py)、
     HearingPoint(app/models/hearing_point.py)
ドライバ: 各テスト関数
スタブ不要 ── インメモリ SQLite の db_session を使う(永続化そのものが対象のため)。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.hearing import POINT_DEFS, PointState, initial_states
from app.models.hearing_point import HearingPoint
from app.models.project import Project
from app.models.user import User
from app.repositories.hearing_point import HearingPointRepository


async def _create_project(session: AsyncSession) -> Project:
    user = User(email="points@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    return project


async def test_save_states_inserts_one_row_per_point(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = HearingPointRepository(db_session)

    await repo.save_states(project.id, initial_states().values())

    rows = await repo.list_for_project(project.id)
    assert len(rows) == len(POINT_DEFS)
    assert all(isinstance(row, HearingPoint) and row.status == "open" for row in rows)


async def test_save_states_updates_existing_rows_by_key(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = HearingPointRepository(db_session)
    await repo.save_states(project.id, initial_states().values())

    await repo.save_states(
        project.id, [PointState("purpose", "decided", "工数を減らす", ("id-1", "id-2"))]
    )

    rows = {row.point_key: row for row in await repo.list_for_project(project.id)}
    assert len(rows) == len(POINT_DEFS)
    assert rows["purpose"].status == "decided"
    assert rows["purpose"].summary == "工数を減らす"
    assert rows["purpose"].evidence == ["id-1", "id-2"]
    assert rows["users"].status == "open"
