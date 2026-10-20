"""変更要求(CR)のルートのテスト。

SUT: ルート create_change_request / list_change_requests / get_change_request /
     update_change_request / explain_change_request / decide_change_request /
     withdraw_change_request / copy_change_request / get_change_request_impact /
     get_change_request_explanation / download_change_request_explanation /
     generate_change_request_summary / edit_change_request_summary /
     confirm_change_request_summary(→ ChangeRequestService)と、api_router への登録
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ: ルートがサービスの結果を応答の形に詰めることが検証対象のため、テスト用の SQLite の
セッションと、合意済みの版を作った fixture(`create_impact_project`)を使う。要約だけは LLM の
解決(LlmResolver.resolve)を、固定の文を返すフェイクに差し替える(stub_llm_resolver)
"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.llm_resolver import stub_llm_resolver

from app.ai.llm.catalog import Purpose
from app.api.routes import api_router
from app.api.routes.change_requests import (
    confirm_change_request_summary,
    copy_change_request,
    create_change_request,
    decide_change_request,
    download_change_request_explanation,
    edit_change_request_summary,
    explain_change_request,
    generate_change_request_summary,
    get_change_request,
    get_change_request_explanation,
    get_change_request_impact,
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
    SummaryEdit,
)
from app.services.baseline_service import BaselineService
from app.services.errors import (
    ChangeRequestInvalidTransitionError,
    ChangeRequestNotFoundError,
    ChangeRequestSummaryNotFoundError,
)

_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
_CREATE = ChangeRequestCreate.model_validate(
    {
        "title": "一覧に絞り込みを足す",
        "content": "日付で絞り込む",
        "background_source": "email",
        "targets": [{"requirement_id": "FR-002"}],
    }
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
    # 一覧も合意の記録を持つ(束の作成で、合意の記録の初期値にする)
    assert listed[1].decision is not None and listed[1].decision.reason == "範囲の外"
    assert listed[0].decision is None and listed[0].included_baseline_number is None
    assert fetched.derived_numbers == [2]
    assert [e.action for e in fetched.events] == ["create", "explain", "reject", "copied"]


async def test_routes_raise_for_missing_and_invalid(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    with pytest.raises(ChangeRequestNotFoundError):
        await get_change_request(5, db_session, project)
    with pytest.raises(ChangeRequestInvalidTransitionError):
        await copy_change_request(1, db_session, project, user)


async def test_impact_route_predicts_then_returns_saved(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    before = await get_change_request_impact(1, db_session, project)
    await explain_change_request(1, db_session, project, user)
    await decide_change_request(
        1,
        ChangeRequestDecision(decision="agreed", decided_at=_AT, reason="合う"),
        db_session,
        project,
        user,
    )
    after = await get_change_request_impact(1, db_session, project)

    assert (before.label, before.baseline_label, before.saved) == ("CR-1", "B-1", False)
    assert [(t.requirement_id, t.kind_label, t.text) for t in before.targets] == [
        ("FR-002", "内容の変更", "予約の一覧を見られる")
    ]
    assert {"screen:SCR-001", "fn:F-02", "unit:U-003"} <= {i.key for i in before.items}
    assert [(e.key, e.state) for e in before.excluded] == [("test_spec", "absent")]
    assert after.saved is True
    assert after.items == before.items
    with pytest.raises(ChangeRequestNotFoundError):
        await get_change_request_impact(9, db_session, project)


async def test_explanation_routes(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    preview = await get_change_request_explanation(1, db_session, project)
    download = await download_change_request_explanation(1, db_session, project)

    assert (preview.label, preview.filename) == ("CR-1", "cr-1-explanation.md")
    assert preview.markdown.startswith("# 変更の説明書 CR-1: 一覧に絞り込みを足す")
    assert len(preview.fingerprint) == 64
    assert preview.has_test_spec is False
    assert preview.units == ["M-01-T03"]
    assert download.media_type == "text/markdown"
    assert bytes(download.body).decode() == preview.markdown + "\n"
    assert "cr-1-explanation.md" in download.headers["content-disposition"]
    with pytest.raises(ChangeRequestNotFoundError):
        await get_change_request_explanation(9, db_session, project)


async def test_summary_routes(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """要約は利用者の生成の用途で解決した LLM で作り、照合の結果と状態を返す。確かめると
    ダウンロードに載る。"""
    stub = stub_llm_resolver(monkeypatch, FakeLLM(content="FR-002 と FR-009 を3日で直します。"))
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    with pytest.raises(ChangeRequestSummaryNotFoundError):
        await edit_change_request_summary(1, SummaryEdit(text="要約"), db_session, project)
    generated = await generate_change_request_summary(1, db_session, project, user)
    assert stub.calls == [(user.id, Purpose.GENERATION)]
    assert generated.summary is not None
    assert (generated.summary.state, generated.summary.state_label) == ("unconfirmed", "未確認")
    assert generated.summary.unknown_requirement_ids == ["FR-009"]
    assert generated.summary.unknown_numbers == ["3"]
    assert generated.summary.generated_model == "gemini-3.5-flash-lite"
    assert generated.output == generated.markdown + "\n"

    edited = await edit_change_request_summary(
        1, SummaryEdit(text="FR-002 の一覧を直します。"), db_session, project
    )
    assert edited.summary is not None and edited.summary.edited is True
    assert edited.summary.unknown_requirement_ids == []
    confirmed = await confirm_change_request_summary(1, db_session, project, user)
    assert confirmed.summary is not None and confirmed.summary.state == "confirmed"
    assert confirmed.summary.confirmed_by == user.id
    download = await download_change_request_explanation(1, db_session, project)
    assert "## 要約\n\nFR-002 の一覧を直します。" in bytes(download.body).decode()


def test_router_is_registered() -> None:
    """api_router(app/api/routes/__init__.py)に CR のルーターを登録したこと。"""
    app = FastAPI()
    app.include_router(api_router)
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/change-requests" in paths
    assert "/projects/{project_id}/change-requests/{number}/decision" in paths
    assert "/projects/{project_id}/change-requests/{number}/copy" in paths
    assert "/projects/{project_id}/change-requests/{number}/impact" in paths
    assert "/projects/{project_id}/change-requests/{number}/explanation" in paths
    assert "/projects/{project_id}/change-requests/{number}/explanation/download" in paths
    assert "/projects/{project_id}/change-requests/{number}/explanation/summary" in paths
    assert "/projects/{project_id}/change-requests/{number}/explanation/summary/confirm" in paths
    assert "/projects/{project_id}/change-requests/{number}/comparison" in paths
    assert "/projects/{project_id}/change-requests/{number}/verification" in paths
    assert "/projects/{project_id}/change-requests/{number}/document-checks" in paths
    assert "/projects/{project_id}/change-requests/{number}/verify" in paths
    assert "/projects/{project_id}/change-requests/{number}/review-candidates" in paths
    assert "/projects/{project_id}/change-requests/{number}/reviews" in paths
    assert "/projects/{project_id}/change-requests/{number}/trace" in paths
