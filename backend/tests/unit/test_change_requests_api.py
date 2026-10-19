"""変更要求(CR)のルートのテスト。

SUT: ルート create_change_request / list_change_requests / get_change_request /
     update_change_request / explain_change_request / decide_change_request /
     withdraw_change_request / copy_change_request(→ ChangeRequestService)と、
     api_router への登録
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ不要 ── LLM を呼ばず、ルートがサービスの結果を応答の形に詰めることが検証対象のため、
テスト用の SQLite のセッションと、合意済みの版を作った fixture(`create_impact_project`)を使う
"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project

from app.api.routes import api_router
from app.api.routes.change_requests import (
    copy_change_request,
    create_change_request,
    decide_change_request,
    explain_change_request,
    get_change_request,
    list_change_requests,
    update_change_request,
    withdraw_change_request,
)
from app.models.project import Project
from app.models.user import User
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestUpdate,
    ChangeRequestWithdraw,
)
from app.services.baseline_service import BaselineService
from app.services.errors import ChangeRequestInvalidTransitionError, ChangeRequestNotFoundError

_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
_CREATE = ChangeRequestCreate(
    title="一覧に絞り込みを足す",
    content="日付で絞り込む",
    background_source="email",
    target_requirement_ids=["FR-002"],
)


async def _setup(session: AsyncSession) -> tuple[Project, User]:
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    user = await session.get(User, project.user_id)
    assert user is not None
    return project, user


async def test_routes_run_the_whole_flow(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)

    created = await create_change_request(_CREATE, db_session, project, user)
    edited = await update_change_request(
        1, ChangeRequestUpdate(content="日付と備品で絞り込む"), db_session, project, user
    )
    explained = await explain_change_request(1, db_session, project, user)
    rejected = await decide_change_request(
        1,
        ChangeRequestDecision(decision="rejected", decided_at=_AT, reason="範囲の外"),
        db_session,
        project,
        user,
    )
    copied = await copy_change_request(1, db_session, project, user)
    withdrawn = await withdraw_change_request(
        2, ChangeRequestWithdraw(reason="出し直さない"), db_session, project, user
    )
    listed = await list_change_requests(db_session, project)
    fetched = await get_change_request(1, db_session, project)

    assert (created.label, created.created_by, created.background_source_label) == (
        "CR-1",
        user.id,
        "メール",
    )
    assert edited.content == "日付と備品で絞り込む"
    assert explained.status == "explained"
    assert rejected.decision is not None and rejected.decision.recorded_by == user.id
    assert rejected.allowed_actions == ["copy"]
    assert (copied.label, copied.derived_from_number) == ("CR-2", 1)
    assert withdrawn.status_label == "取り下げ"
    assert [c.label for c in listed] == ["CR-2", "CR-1"]
    assert fetched.derived_numbers == [2]
    assert [e.action for e in fetched.events] == ["create", "explain", "reject", "copied"]


async def test_routes_raise_for_missing_and_invalid(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    with pytest.raises(ChangeRequestNotFoundError):
        await get_change_request(5, db_session, project)
    with pytest.raises(ChangeRequestInvalidTransitionError):
        await copy_change_request(1, db_session, project, user)


def test_router_is_registered() -> None:
    """api_router(app/api/routes/__init__.py)に CR のルーターを登録したこと。"""
    app = FastAPI()
    app.include_router(api_router)
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/change-requests" in paths
    assert "/projects/{project_id}/change-requests/{number}/decision" in paths
    assert "/projects/{project_id}/change-requests/{number}/copy" in paths
