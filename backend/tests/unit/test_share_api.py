"""CR の共有リンクのルート(開発者の作成・一覧・取り消し・延長と、ログインの要らない閲覧・
コメント、開発者のコメント欄)のテスト。

SUT: ルート create_change_request_share_link / list_change_request_share_links /
     revoke_change_request_share_link / extend_change_request_share_link(app/api/routes/
     change_requests.py)・get_shared_change_request(app/api/routes/share.py)と api_router への
     登録、応答 ShareLinkRead・ShareLinkCreatedRead・SharedChangeRequestRead・SharedOptionRead・
     SharedDecisionRead・SectionChangeRead、設定 SHARE_RATE_LIMIT_PER_IP_PER_HOUR、
     comment_on_shared_change_request(share.py)・list_change_request_comments /
     reply_change_request_comment(change_requests.py)、応答 SharedCommentRead・
     ChangeRequestCommentRead、設定 SHARE_WRITE_RATE_LIMIT_PER_IP_PER_HOUR、
     decide_shared_change_request(share.py)、応答 SharedDecisionRead の出どころ・
     ChangeRequestDecisionRead の出どころと指紋(get_change_request)
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ: ルートがサービスの結果を応答の形に詰めることが検証対象のため、テスト用の SQLite の
セッションと、合意済みの版を作った fixture(`create_impact_project`)を使う。公開の入口の回数の
上限は FakeRedis
"""

from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.fake_redis import FakeRedis

from app.api.routes import api_router
from app.api.routes.change_requests import (
    create_change_request_share_link,
    decide_change_request,
    extend_change_request_share_link,
    get_change_request,
    list_change_request_comments,
    list_change_request_share_links,
    reply_change_request_comment,
    revoke_change_request_share_link,
    update_change_request,
)
from app.api.routes.share import (
    comment_on_shared_change_request,
    decide_shared_change_request,
    get_shared_change_request,
)
from app.core.config import settings
from app.models.project import Project
from app.models.user import User
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestUpdate,
)
from app.schemas.share_link import (
    ChangeRequestReplyCreate,
    SharedCommentCreate,
    SharedDecisionCreate,
    ShareLinkCreate,
    ShareLinkExtend,
)
from app.services.baseline_service import BaselineService
from app.services.change_request_service import ChangeRequestService
from app.services.errors import (
    ChangeRequestOptionInvalidError,
    RateLimitExceededError,
    ShareLinkRevokedError,
)

_AT = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
_CREATE = ChangeRequestCreate.model_validate(
    {
        "title": "登録の確認を足す",
        "content": "予約の登録の前に確認を挟む",
        "background_source": "meeting",
        "targets": [{"requirement_id": "FR-001"}, {"requirement_id": "FR-002"}],
        "options": {
            "items": [
                {
                    "name": "登録だけ",
                    "targets": [{"requirement_id": "FR-001", "proposed_text": "登録で確認する"}],
                    "outcome": "手間が小さい",
                },
                {
                    "name": "登録と一覧",
                    "targets": [{"requirement_id": "FR-001"}, {"requirement_id": "FR-002"}],
                    "outcome": "一覧も直す",
                },
            ],
            "recommended": 1,
            "reason": "早く出せる",
        },
    }
)


async def _setup(session: AsyncSession) -> tuple[Project, User]:
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    user = await session.get(User, project.user_id)
    assert user is not None
    await ChangeRequestService(session).create(project, _CREATE, created_by=user.id)
    return project, user


# --- 103-2: 開発者のルート ---


async def test_developer_share_link_routes(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)

    created = await create_change_request_share_link(
        1, ShareLinkCreate(expires_in_days=7), db_session, project, user
    )
    extended = await extend_change_request_share_link(
        1, created.id, ShareLinkExtend(expires_in_days=30), db_session, project, user
    )
    listed = await list_change_request_share_links(1, db_session, project)
    revoked = await revoke_change_request_share_link(1, created.id, db_session, project, user)

    assert created.path == f"/share/{created.token}"
    assert (created.state, created.state_label) == ("open", "開いている")
    assert created.summary_needs_reconfirm is False
    assert extended.expires_at > created.expires_at
    assert [link.id for link in listed] == [created.id]
    assert "token" not in listed[0].model_dump()
    assert (revoked.state, revoked.revoked_at is not None) == ("revoked", True)


# --- 103-3: 公開の閲覧のルート ---


async def test_public_route_reads_without_login(db_session: AsyncSession) -> None:
    """ログインなしで、説明書・案・見た版の指紋を読める。内部の ID は出さない。"""
    project, user = await _setup(db_session)
    created = await create_change_request_share_link(
        1, ShareLinkCreate(), db_session, project, user
    )

    read = await get_shared_change_request(
        created.token, db_session, cast(Redis, FakeRedis()), "10.0.0.1"
    )

    assert (read.state, read.label, read.title) == ("open", "CR-1", "登録の確認を足す")
    assert read.markdown is not None and "## 3. 選択肢" in read.markdown
    assert read.fingerprint is not None and len(read.fingerprint) == 64
    assert [(o.label, o.name, o.recommended) for o in read.options] == [
        ("案1", "登録だけ", True),
        ("案2", "登録と一覧", False),
    ]
    assert read.recommended_reason == "早く出せる"
    assert read.decision is None
    dumped = read.model_dump()
    assert "project_id" not in dumped and "id" not in dumped


async def test_public_route_paused_changes_and_decision(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)
    created = await create_change_request_share_link(
        1, ShareLinkCreate(), db_session, project, user
    )
    redis = cast(Redis, FakeRedis())
    await update_change_request(
        1, ChangeRequestUpdate(title="登録と変更の確認を足す"), db_session, project, user
    )

    paused = await get_shared_change_request(created.token, db_session, redis, "10.0.0.1")
    await ChangeRequestService(db_session).explain(project, 1, actor_id=user.id)
    reopened = await get_shared_change_request(created.token, db_session, redis, "10.0.0.1")
    await decide_change_request(
        1,
        ChangeRequestDecision(decision="agreed", decided_at=_AT, reason="合う", option=2),
        db_session,
        project,
        user,
    )
    decided = await get_shared_change_request(created.token, db_session, redis, "10.0.0.1")

    assert (paused.state, paused.state_label, paused.markdown) == ("paused", "直しています", None)
    assert paused.options == []
    assert [c.name for c in reopened.changes] == ["題と基準"]
    assert reopened.changed_since is not None
    assert decided.state == "decided" and decided.changes == []
    assert decided.decision is not None
    assert (decided.decision.decision_label, decided.decision.option) == ("合意", "案2 登録と一覧")


async def test_public_route_errors_and_rate_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, user = await _setup(db_session)
    created = await create_change_request_share_link(
        1, ShareLinkCreate(), db_session, project, user
    )
    await revoke_change_request_share_link(1, created.id, db_session, project, user)
    redis = cast(Redis, FakeRedis())
    monkeypatch.setattr(settings, "SHARE_RATE_LIMIT_PER_IP_PER_HOUR", 1)

    with pytest.raises(ShareLinkRevokedError):
        await get_shared_change_request(created.token, db_session, redis, "10.0.0.9")
    # 見つからない・使えないリンクも数える(引く前に数える)
    with pytest.raises(RateLimitExceededError):
        await get_shared_change_request(created.token, db_session, redis, "10.0.0.9")


def test_router_is_registered() -> None:
    """共有リンクのルート(開発者の4つと公開の入口)を api_router に登録したこと。"""
    app = FastAPI()
    app.include_router(api_router)
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/change-requests/{number}/share-links" in paths
    assert "/projects/{project_id}/change-requests/{number}/share-links/{link_id}/revoke" in paths
    assert "/projects/{project_id}/change-requests/{number}/share-links/{link_id}/extend" in paths
    assert "/share/{token}" in paths
    # 公開の入口には認証を付けない
    assert "security" not in paths["/share/{token}"]["get"]
    assert "/share/{token}/comments" in paths
    assert "security" not in paths["/share/{token}/comments"]["post"]
    assert "/projects/{project_id}/change-requests/{number}/comments" in paths
    assert (
        "/projects/{project_id}/change-requests/{number}/comments/{comment_id}/replies" in paths
    )
    assert "/share/{token}/decision" in paths
    assert "security" not in paths["/share/{token}/decision"]["post"]



# --- 104-2: コメント欄のルート ---


async def test_comment_routes(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開の入口でコメントし、開発者が返信する。クライアントには返信の書き手を「開発者」と出す。"""
    project, user = await _setup(db_session)
    created = await create_change_request_share_link(
        1, ShareLinkCreate(), db_session, project, user
    )
    redis = cast(Redis, FakeRedis())
    read = await get_shared_change_request(created.token, db_session, redis, "10.0.0.1")
    assert read.fingerprint is not None

    comment = await comment_on_shared_change_request(
        created.token,
        SharedCommentCreate(name="山田", body="案1で足りますか", fingerprint=read.fingerprint),
        db_session,
        redis,
        "10.0.0.1",
    )
    listed = await list_change_request_comments(1, db_session, project)
    replied = await reply_change_request_comment(
        1, listed[0].id, ChangeRequestReplyCreate(body="足ります"), db_session, project, user
    )
    shared = await get_shared_change_request(created.token, db_session, redis, "10.0.0.1")

    assert (comment.author, comment.from_developer, comment.replies) == ("山田", False, [])
    assert (listed[0].author_name, listed[0].can_reply) == ("山田", True)
    assert [(r.author_id, r.can_reply) for r in replied.replies] == [(user.id, False)]
    assert [(c.author, [r.author for r in c.replies]) for c in shared.comments] == [
        ("山田", ["開発者"])
    ]
    assert "author_id" not in shared.comments[0].model_dump()

    monkeypatch.setattr(settings, "SHARE_WRITE_RATE_LIMIT_PER_IP_PER_HOUR", 1)
    with pytest.raises(RateLimitExceededError):
        await comment_on_shared_change_request(
            created.token,
            SharedCommentCreate(name="山田", body="もう1つ", fingerprint=read.fingerprint),
            db_session,
            redis,
            "10.0.0.1",
        )



# --- 104-3: 共有リンクからの合意・却下のルート ---


async def test_decision_route_with_options(db_session: AsyncSession) -> None:
    """案のある CR は、案を選ばずに合意できない(開発者と同じ規則)。選んだ案で合意すると、判断の
    結果に出どころが出て、開発者の CR の詳細にも出どころと見た版が残る。"""
    project, user = await _setup(db_session)
    created = await create_change_request_share_link(
        1, ShareLinkCreate(), db_session, project, user
    )
    redis = cast(Redis, FakeRedis())
    read = await get_shared_change_request(created.token, db_session, redis, "10.0.0.1")
    assert read.fingerprint is not None

    with pytest.raises(ChangeRequestOptionInvalidError):
        await decide_shared_change_request(
            created.token,
            SharedDecisionCreate(decision="agreed", name="山田", fingerprint=read.fingerprint),
            db_session,
            redis,
            "10.0.0.1",
        )
    decided = await decide_shared_change_request(
        created.token,
        SharedDecisionCreate(
            decision="agreed", name="山田", fingerprint=read.fingerprint, option=2
        ),
        db_session,
        redis,
        "10.0.0.1",
    )
    detail = await get_change_request(1, db_session, project)

    assert decided.state == "decided"
    assert decided.decision is not None
    assert (decided.decision.counterpart, decided.decision.option) == ("山田", "案2 登録と一覧")
    assert (decided.decision.source, decided.decision.source_label) == ("share_link", "共有リンク")
    assert detail.decision is not None
    assert (detail.decision.source, detail.decision.option) == ("share_link", 2)
    assert detail.decision.fingerprint == read.fingerprint
