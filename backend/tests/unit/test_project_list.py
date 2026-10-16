import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.routes.projects import router as projects_router
from app.core.database import get_db
from app.models.project import Project
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.schemas.project import PROJECT_PAGE_SIZE
from app.services.project import ProjectService

_BASE = datetime(2026, 1, 1, tzinfo=UTC)


async def _user(session: AsyncSession) -> User:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    return user


async def _project(
    session: AsyncSession,
    user: User,
    title: str,
    *,
    mode: str = "simple",
    status: str = "interviewing",
    minutes: int = 0,
) -> Project:
    # updated_at を明示する(SQLite の CURRENT_TIMESTAMP は秒精度で、並びが揺れるため)
    project = Project(
        user_id=user.id,
        title=title,
        mode=mode,
        status=status,
        updated_at=_BASE + timedelta(minutes=minutes),
    )
    session.add(project)
    await session.flush()
    return project


async def test_search_matches_title_partially_ignoring_case(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    hit = await _project(db_session, user, "Equipment 予約")
    await _project(db_session, user, "勤怠管理")

    items, total = await ProjectRepository(db_session).search_for_user(user.id, q="equip")

    assert [p.id for p in items] == [hit.id]
    assert total == 1


async def test_search_treats_like_wildcards_as_text(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    percent = await _project(db_session, user, "達成率100%")
    underscore = await _project(db_session, user, "a_b")
    await _project(db_session, user, "達成率1000")
    await _project(db_session, user, "axb")
    repo = ProjectRepository(db_session)

    assert [p.id for p in (await repo.search_for_user(user.id, q="100%"))[0]] == [percent.id]
    assert [p.id for p in (await repo.search_for_user(user.id, q="a_b"))[0]] == [underscore.id]


async def test_mode_and_statuses_are_combined_with_and(db_session: AsyncSession) -> None:
    """status は複数のどれかに一致(OR)、mode と status は AND。"""
    user = await _user(db_session)
    a = await _project(db_session, user, "a", mode="detailed", status="interviewing", minutes=3)
    b = await _project(db_session, user, "b", mode="detailed", status="completed", minutes=2)
    await _project(db_session, user, "c", mode="detailed", status="generating", minutes=1)
    await _project(db_session, user, "d", mode="simple", status="completed")
    repo = ProjectRepository(db_session)

    items, total = await repo.search_for_user(
        user.id, mode="detailed", statuses=["interviewing", "completed"]
    )

    assert [p.id for p in items] == [a.id, b.id]
    assert total == 2


async def test_deleted_and_other_users_projects_are_excluded(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    other = await _user(db_session)
    kept = await _project(db_session, user, "予約")
    deleted = await _project(db_session, user, "予約2")
    await _project(db_session, other, "予約3")
    await ProjectRepository(db_session).mark_deleted(deleted)

    items, total = await ProjectRepository(db_session).search_for_user(user.id, q="予約")

    assert [p.id for p in items] == [kept.id]
    assert total == 1


async def test_list_page_slices_by_ten_in_updated_order(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    projects = [await _project(db_session, user, f"p{i}", minutes=i) for i in range(12)]
    newest_first = [p.id for p in reversed(projects)]
    service = ProjectService(db_session)

    first = await service.list_page(user.id)
    second = await service.list_page(user.id, page=2)
    beyond = await service.list_page(user.id, page=3)

    assert [p.id for p in first.items] == newest_first[:PROJECT_PAGE_SIZE]
    assert [p.id for p in second.items] == newest_first[PROJECT_PAGE_SIZE:]
    assert first.total == second.total == 12
    assert beyond.items == []
    assert beyond.total == 12


async def test_list_page_ignores_blank_keyword(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    await _project(db_session, user, "予約")
    await _project(db_session, user, "勤怠")
    service = ProjectService(db_session)

    assert (await service.list_page(user.id, q="   ")).total == 2
    assert (await service.list_page(user.id, q=" 予約 ")).total == 1


def _app(session: AsyncSession, user: User) -> FastAPI:
    """一覧のルートだけを載せたアプリ(DB のセッションとログインの利用者を差し替える)。"""
    app = FastAPI()
    app.include_router(projects_router, prefix="/api/v1")

    async def _db() -> AsyncGenerator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return app


async def test_route_reads_repeated_status_and_returns_page(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    a = await _project(db_session, user, "予約A", status="interviewing", minutes=2)
    b = await _project(db_session, user, "予約B", status="revising", minutes=1)
    await _project(db_session, user, "予約C", status="completed")
    transport = ASGITransport(app=_app(db_session, user))

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/projects",
            params={"q": "予約", "mode": "simple", "status": ["interviewing", "revising"]},
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(a.id), str(b.id)]
    assert body["total"] == 2


async def test_route_rejects_invalid_parameters(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    transport = ASGITransport(app=_app(db_session, user))

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        bad_status = await client.get("/api/v1/projects", params={"status": "archived"})
        bad_page = await client.get("/api/v1/projects", params={"page": 0})
        bad_mode = await client.get("/api/v1/projects", params={"mode": "other"})

    assert bad_status.status_code == 422
    assert bad_page.status_code == 422
    assert bad_mode.status_code == 422
