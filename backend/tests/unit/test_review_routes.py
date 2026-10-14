"""設計レビューの API(ユーザーの設定・区切りの一覧・区切りの最新の回・「もう一度レビュー」・
指摘の採否)のテスト。

SUT はルートの関数(users.read_current_user・update_review_settings、reviews.list_review_gates・
get_latest_review・request_review・decide_finding)と UserRead の実効値。ドライバは pytest で
ルートの関数を直接呼ぶ(既存のルートのテストと同じ形)。スタブは FakeRedis(1日の回数)と
BackgroundTasks(実行は積むだけで流さない)。文書・指摘は偽の LLM で作っておく。
"""

import uuid

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_stage7_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.fake_redis import FakeRedis
from tests.fixtures.review import review_output

from app.api.routes.reviews import (
    decide_finding,
    execute_review,
    get_latest_review,
    list_review_gates,
    request_review,
)
from app.api.routes.users import read_current_user, update_review_settings
from app.core.config import settings
from app.models.project import Project
from app.models.review import ReviewRun
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.schemas.review import FindingDecisionUpdate
from app.schemas.user import ReviewSettingsUpdate
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import ReviewFindingNotFoundError, ReviewNotAllowedError
from app.services.review_service import ReviewService


@pytest.fixture(autouse=True)
def _fake_review_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    # 「もう一度レビュー」の受け付けはキーの有無を見る。E2E の偽物の設定で通す(実行はしない)
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", True)


async def _user(session: AsyncSession, *, superuser: bool) -> User:
    user = User(
        email=f"u-{uuid.uuid4()}@example.com", hashed_password="x", is_superuser=superuser
    )
    session.add(user)
    await session.flush()
    return user


async def _generated_project(session: AsyncSession, user: User) -> Project:
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    llm = FakeLLM(content_sequence=["## 自己診断"], structured_by_schema=upstream_outputs())
    await DocGeneratorService(session).generate(project.id, user.id, llm=llm)
    return project


async def test_current_user_shows_review_setting_and_effective(db_session: AsyncSession) -> None:
    user = await _user(db_session, superuser=True)

    read = await read_current_user(user)
    assert (read.review_enabled, read.review_effective) == (False, False)

    updated = await update_review_settings(
        ReviewSettingsUpdate(review_enabled=True), db_session, user
    )
    assert (updated.review_enabled, updated.review_effective) == (True, True)
    assert updated.model_dump()["review_effective"] is True


async def test_non_superuser_cannot_change_review_setting(db_session: AsyncSession) -> None:
    user = await _user(db_session, superuser=False)

    with pytest.raises(ReviewNotAllowedError):
        await update_review_settings(ReviewSettingsUpdate(review_enabled=True), db_session, user)
    assert user.review_enabled is False


async def test_latest_review_is_none_before_any_review(db_session: AsyncSession) -> None:
    user = await _user(db_session, superuser=True)
    project = await _generated_project(db_session, user)

    assert await get_latest_review("documents", db_session, project) is None


async def test_request_review_queues_execution_and_latest_is_current(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, superuser=True)
    project = await _generated_project(db_session, user)
    user.review_enabled = True
    tasks = BackgroundTasks()

    accepted = await request_review(
        "documents", db_session, FakeRedis(), project, user, tasks  # type: ignore[arg-type]
    )

    assert accepted.status == "running" and accepted.trigger == "manual"
    assert [t.func for t in tasks.tasks] == [execute_review]
    latest = await get_latest_review("documents", db_session, project)
    assert latest is not None and latest.id == accepted.id
    assert latest.is_current is True

    # 文書が変わった後(レビューした版が表示中の版と違う)は、今の文書のレビューではない
    run = await db_session.get(ReviewRun, accepted.id)
    assert run is not None
    run.targets = {**run.targets, "requirements": 0}
    await db_session.flush()
    stale = await get_latest_review("documents", db_session, project)
    assert stale is not None and stale.is_current is False


async def test_request_review_rejects_user_without_review(db_session: AsyncSession) -> None:
    user = await _user(db_session, superuser=True)
    project = await _generated_project(db_session, user)

    with pytest.raises(ReviewNotAllowedError):
        await request_review(
            "documents", db_session, FakeRedis(), project, user, BackgroundTasks()  # type: ignore[arg-type]
        )


async def test_list_review_gates_shows_running_and_current(db_session: AsyncSession) -> None:
    user = await _user(db_session, superuser=True)
    project = await _generated_project(db_session, user)
    user.review_enabled = True

    before = await list_review_gates(db_session, project)
    assert [(g.gate, g.ready, g.status, g.run) for g in before] == [
        ("documents", True, "unreviewed", None)
    ]

    accepted = await request_review(
        "documents", db_session, FakeRedis(), project, user, BackgroundTasks()  # type: ignore[arg-type]
    )
    [running] = await list_review_gates(db_session, project)
    assert running.status == "running"
    assert running.run is not None and running.run.id == accepted.id and running.run.is_current


async def test_request_stage6_review_on_detailed_project(db_session: AsyncSession) -> None:
    project = await create_stage7_project(db_session)
    user = await db_session.get(User, project.user_id)
    assert user is not None
    user.is_superuser = True
    user.review_enabled = True

    accepted = await request_review(
        "stage6", db_session, FakeRedis(), project, user, BackgroundTasks()  # type: ignore[arg-type]
    )

    assert accepted.gate == "stage6" and accepted.targets["stage:6"] == 1
    gates = {g.gate: g.status for g in await list_review_gates(db_session, project)}
    assert gates == {"documents": "unreviewed", "stage6": "running", "stage7": "not_ready"}


async def test_decide_finding_changes_decision(db_session: AsyncSession) -> None:
    user = await _user(db_session, superuser=True)
    project = await _generated_project(db_session, user)
    user.review_enabled = True
    service = ReviewService(db_session, llm=FakeLLM(structured_sequence=[review_output()]))
    run = await service.start_review(project, user, gate="documents", trigger="manual")
    finished = await service.execute(run.id)
    assert finished is not None
    finding_id = finished.findings[0].id

    read = await decide_finding(
        finding_id, FindingDecisionUpdate(decision="held"), db_session, project
    )

    assert read.id == finding_id and read.decision == "held"
    latest = await get_latest_review("documents", db_session, project)
    assert latest is not None and latest.findings[0].decision == "held"
    other = await _generated_project(db_session, user)
    with pytest.raises(ReviewFindingNotFoundError):
        await decide_finding(
            finding_id, FindingDecisionUpdate(decision="adopted"), db_session, other
        )
