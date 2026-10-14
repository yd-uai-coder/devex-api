"""設計レビューの回と指摘の永続化(ReviewRunRepository・モデル・users の設定の列)のテスト。

SUT は ReviewRunRepository、ドライバは pytest(`db_session` fixture)。スタブ不要 ── 実 DB
(インメモリの SQLite)に書いて読み直す。
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.review import review_output

from app.models.project import Project
from app.models.review import ReviewFinding, ReviewRun
from app.models.user import User
from app.repositories.review import ReviewRunRepository


async def _project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    return project


async def test_user_review_setting_defaults_to_off(db_session: AsyncSession) -> None:
    user = User(email="new@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()

    assert user.review_enabled is False


async def test_finish_saves_findings_in_level_order_with_tokens(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    repo = ReviewRunRepository(db_session)
    run = await repo.create_run(
        project_id=project.id,
        gate="documents",
        targets={"requirements": 2, "external_design": 1},
        trigger="auto",
        model="claude-test",
    )
    usage = {
        "input_tokens": 7000,
        "output_tokens": 900,
        "input_token_details": {"cache_read": 0, "cache_creation": 0},
    }

    await repo.finish(run, status="succeeded", usage=usage, findings=review_output().findings)
    db_session.expunge_all()
    loaded = await repo.get_latest(project_id=project.id, gate="documents")

    assert loaded is not None
    assert loaded.status == "succeeded" and loaded.finished_at is not None
    assert loaded.targets == {"requirements": 2, "external_design": 1}
    assert (loaded.input_tokens, loaded.output_tokens, loaded.cache_read_tokens) == (7000, 900, 0)
    assert [f.level for f in loaded.findings] == ["critical", "major", "minor"]
    assert [f.position for f in loaded.findings] == [0, 1, 2]
    assert loaded.findings[1].point_key == "security"
    assert all(f.decision == "pending" for f in loaded.findings)


async def test_latest_and_running(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    repo = ReviewRunRepository(db_session)
    first = await repo.create_run(
        project_id=project.id, gate="documents", targets={}, trigger="auto", model="m"
    )
    await repo.finish(first, status="failed", error_code="REVIEW_UNAVAILABLE")
    second = await repo.create_run(
        project_id=project.id, gate="documents", targets={}, trigger="manual", model="m"
    )

    latest = await repo.get_latest(project_id=project.id, gate="documents")

    assert latest is not None and latest.id == second.id
    assert [r.id for r in await repo.list_running(project.id)] == [second.id]
    assert first.error_code == "REVIEW_UNAVAILABLE" and first.findings == []


async def test_deleting_project_deletes_runs_and_findings(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    repo = ReviewRunRepository(db_session)
    run = await repo.create_run(
        project_id=project.id, gate="documents", targets={}, trigger="auto", model="m"
    )
    await repo.finish(run, status="succeeded", findings=review_output().findings)

    await db_session.refresh(project, ["review_runs"])
    await db_session.delete(project)
    await db_session.flush()

    assert await repo.count() == 0
    assert await db_session.get(ReviewFinding, run.findings[0].id) is None
    assert await db_session.get(ReviewRun, run.id) is None
