"""プロジェクトの削除(削除の印・取得から外す・物理削除・ルート)のテスト。

SUT: ProjectRepository(mark_deleted / is_live / purge_deleted と、取得・一覧の条件)・
     ProjectService.delete・app/services/project_deletion(project_vanished / purge_deleted /
     purge_deleted_projects)・ルート delete_project と依存関数 get_current_project
ドライバ: 各テスト関数
スタブ: FakeRedis(生成の進み具合の鍵が消えることを見る)。`purge_deleted_projects` のテストだけ、
自前で開くセッションの工場(`AsyncSessionLocal`)をテストの DB に差し替える。
DB はインメモリの SQLite(印の付いた行が取得から外れることそのものが検証対象のため、スタブにしない)。
SQLite では外部キーの CASCADE とロックは効かないので、子の行が消えること・ロックを飛ばすことは
integration(tests/integration/test_project_delete_flow.py)で確かめる。
"""

import uuid

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from structlog.testing import capture_logs
from tests.fixtures.fake_redis import FakeRedis

from app.api.deps import get_current_project
from app.api.routes.projects import delete_project, list_projects
from app.models.project import Project
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.services import project_deletion
from app.services.errors import ProjectNotFoundError
from app.services.generation_progress import GenerationProgress, GenerationProgressStore
from app.services.project import ProjectService
from app.services.project_deletion import (
    project_vanished,
    purge_deleted,
    purge_deleted_projects,
)


async def _project(session: AsyncSession, *, email: str = "owner@example.com") -> Project:
    user = User(email=email, hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="備品予約")
    session.add(project)
    await session.flush()
    return project


async def test_marked_project_is_hidden_from_get_and_list(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    kept = Project(user_id=project.user_id, title="残す")
    db_session.add(kept)
    await db_session.flush()
    repo = ProjectRepository(db_session)

    await repo.mark_deleted(project)

    assert project.deleted_at is not None
    assert await repo.get_by_id(project.id, user_id=project.user_id) is None
    assert [p.id for p in await repo.list_for_user(project.user_id)] == [kept.id]
    assert await repo.is_live(project.id) is False
    assert await repo.is_live(kept.id) is True
    assert await repo.is_live(uuid.uuid4()) is False


async def test_service_marks_and_clears_progress(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    redis = FakeRedis()
    store = GenerationProgressStore(redis)  # type: ignore[arg-type]
    await store.write(project.id, GenerationProgress(targets=["requirements"]))

    await ProjectService(db_session).delete(project, redis=redis)  # type: ignore[arg-type]

    assert await store.read(project.id) is None
    assert await ProjectRepository(db_session).is_live(project.id) is False


async def test_route_returns_after_marking_and_schedules_purge(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    user = await db_session.get(User, project.user_id)
    assert user is not None
    tasks = BackgroundTasks()

    current = await get_current_project(project.id, db_session, user)
    await delete_project(db_session, FakeRedis(), current, tasks)  # type: ignore[arg-type]

    [task] = tasks.tasks
    assert task.func is purge_deleted_projects
    # 削除した後は、取得(すべての /projects/{id}/... が通る依存関数)も一覧も見えない
    with pytest.raises(ProjectNotFoundError):
        await get_current_project(project.id, db_session, user)
    assert await list_projects(db_session, user) == []


async def test_other_users_project_cannot_be_deleted(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    other = User(email="other@example.com", hashed_password="x")
    db_session.add(other)
    await db_session.flush()

    # 他人のプロジェクトは依存関数で404になり、ルートまで届かない
    with pytest.raises(ProjectNotFoundError):
        await get_current_project(project.id, db_session, other)
    assert await ProjectRepository(db_session).is_live(project.id) is True


async def test_project_vanished_logs_only_when_gone(db_session: AsyncSession) -> None:
    project = await _project(db_session)

    with capture_logs() as logs:
        assert await project_vanished(db_session, project.id, job="test") is False
        await ProjectRepository(db_session).mark_deleted(project)
        assert await project_vanished(db_session, project.id, job="test") is True

    assert [log["event"] for log in logs] == ["project_deleted_during_job"]
    assert logs[0]["job"] == "test"


async def test_purge_deletes_only_marked_rows(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    kept = Project(user_id=project.user_id, title="残す")
    db_session.add(kept)
    await db_session.flush()
    await ProjectRepository(db_session).mark_deleted(project)
    await db_session.commit()

    assert await purge_deleted(db_session) == 1
    assert await purge_deleted(db_session) == 0

    db_session.expunge_all()
    assert await db_session.get(Project, project.id) is None
    assert await db_session.get(Project, kept.id) is not None


async def test_purge_deleted_projects_opens_its_own_session(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _project(db_session)
    await ProjectRepository(db_session).mark_deleted(project)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(project_deletion, "AsyncSessionLocal", factory)

    await purge_deleted_projects()

    db_session.expunge_all()
    assert await db_session.get(Project, project.id) is None


async def test_purge_deleted_projects_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken() -> AsyncSession:
        raise RuntimeError("db down")

    monkeypatch.setattr(project_deletion, "AsyncSessionLocal", _broken)

    with capture_logs() as logs:
        await purge_deleted_projects()

    assert logs[-1]["event"] == "project_purge_failed"
