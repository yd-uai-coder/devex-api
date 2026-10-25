"""CR の共有リンクの作成・一覧・取り消し・延長と、説明済みにした時点の説明書の写し、ログインの
要らない閲覧のテスト。

SUT: ShareLinkService(create / list_for / revoke / extend / view)・ShareLinkView・
     CreatedShareLink・SharedView、ChangeRequestService.record_explain(explain の写し)・
     explanation_of・load、ChangeRequestShareLinkRepository と ChangeRequestShareLink の ORM 定義、
     状態の操作 share / unshare / extend_share とたどる記録の名前(ACTION_LABEL)、
     ChangeRequestEventRead(説明の行の md の写しを出さない)、スキーマ ShareLinkCreate・
     ShareLinkExtend、エラー ShareLinkNotFoundError・ShareLinkExpiredError・
     ShareLinkRevokedError・ShareLinkNotExtendableError(GoneError)、ShareRateLimiter、
     コメント欄 ShareLinkService.comment / comments_for / reply・comment_facts、
     ChangeRequestCommentRepository と ChangeRequestComment の ORM 定義、操作 comment /
     reply_comment、スキーマ SharedCommentCreate・ChangeRequestReplyCreate、エラー
     ShareLinkNotWritableError・ShareExplanationChangedError・ChangeRequestCommentNotFoundError・
     ChangeRequestCommentNotRepliableError、ShareRateLimiter.enforce_write、
     共有リンクの判断 ShareLinkService.decide・ChangeRequestService.record_decision(decide も
     出どころと指紋を残す)・trace(合意の行のメモの出どころ)、スキーマ SharedDecisionCreate、
     エラー ShareDecisionReasonRequiredError、ChangeRequest の decision_source・decision_fingerprint
ドライバ: pytest のテスト関数
スタブ: 永続化と遷移が検証対象のため、テスト用の SQLite のセッションと、要件 FR-001・FR-002 と
承認済みの段階を持つ fixture(`create_impact_project`)から作った合意済みの版を使う。回数の上限は
FakeRedis(incr / expire / pipeline)
"""

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.fake_redis import FakeRedis

from app.change_requests import ACTION_LABEL, SNAPSHOT_MARKDOWN_KEY
from app.core.errors import GoneError
from app.models.change_request_comment import ChangeRequestComment
from app.models.change_request_share_link import ChangeRequestShareLink
from app.models.project import Project
from app.repositories.change_request_comment import ChangeRequestCommentRepository
from app.repositories.change_request_share_link import ChangeRequestShareLinkRepository
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestEventRead,
    ChangeRequestUpdate,
    ChangeRequestWithdraw,
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
    ChangeRequestCommentNotFoundError,
    ChangeRequestCommentNotRepliableError,
    ChangeRequestInvalidTransitionError,
    RateLimitExceededError,
    ShareDecisionReasonRequiredError,
    ShareExplanationChangedError,
    ShareLinkExpiredError,
    ShareLinkNotExtendableError,
    ShareLinkNotFoundError,
    ShareLinkNotWritableError,
    ShareLinkRevokedError,
)
from app.services.share_link_service import CreatedShareLink, ShareLinkService, comment_facts
from app.services.share_rate_limit import ShareRateLimiter

_AT = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
_CREATE = ChangeRequestCreate.model_validate(
    {
        "title": "登録の確認を足す",
        "content": "予約の登録の前に確認を挟む",
        "background_source": "meeting",
        "targets": [{"requirement_id": "FR-001", "proposed_text": "登録で確認する"}],
    }
)


async def _project(session: AsyncSession) -> Project:
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    await ChangeRequestService(session).create(project, _CREATE, created_by=None)
    return project


async def _share(session: AsyncSession, project: Project, days: int = 14) -> CreatedShareLink:
    return await ShareLinkService(session).create(
        project, 1, ShareLinkCreate(expires_in_days=days), actor_id=None
    )


# --- 103-2: 作成・一覧・取り消し・延長と、説明の写し ---


async def test_create_on_draft_explains_and_keeps_snapshot(db_session: AsyncSession) -> None:
    """下書きの CR にリンクを作ると説明済みになり、その時点の説明書の写しを時系列に残す。"""
    project = await _project(db_session)

    created = await _share(db_session, project)

    change_request = await ChangeRequestService(db_session).load(project, 1)
    explain = change_request.events[-1]
    explanation = (
        await ChangeRequestService(db_session).explanation_of(change_request)
    ).explanation
    assert change_request.status == "explained"
    assert (explain.action, explain.note) == ("explain", "共有リンクを作った")
    assert explain.refs == {
        SNAPSHOT_MARKDOWN_KEY: explanation.markdown,
        "fingerprint": explanation.fingerprint,
    }
    assert explain.refs is not None
    assert "CR の状態: 説明済み" in explain.refs[SNAPSHOT_MARKDOWN_KEY]
    # CR の詳細の応答には md の写しを出さない
    read = ChangeRequestEventRead.model_validate(explain)
    assert read.refs == {"fingerprint": explanation.fingerprint}
    assert len(created.token) == 43
    assert created.view.state == "open"
    assert created.summary_needs_reconfirm is False
    expires = created.view.link.expires_at.replace(tzinfo=UTC)
    assert timedelta(days=13) < expires - datetime.now(UTC) <= timedelta(days=14)


async def test_explain_api_also_keeps_snapshot(db_session: AsyncSession) -> None:
    """リンクを使わずに説明済みにしたときも写しを残す(前回から変わった点の材料)。"""
    project = await _project(db_session)

    view = await ChangeRequestService(db_session).explain(project, 1, actor_id=None)

    refs = view.change_request.events[-1].refs
    assert refs is not None and SNAPSHOT_MARKDOWN_KEY in refs


async def test_recreate_revokes_previous_and_records_share_row(db_session: AsyncSession) -> None:
    """説明済みの CR に作り直すと、前のリンクを取り消し、状態を変えない記録だけの行を足す。"""
    project = await _project(db_session)
    first = await _share(db_session, project)

    second = await _share(db_session, project, days=30)

    links = await ShareLinkService(db_session).list_for(project, 1)
    change_request = await ChangeRequestService(db_session).load(project, 1)
    assert [v.link.id for v in links] == [second.view.link.id, first.view.link.id]
    assert [v.state for v in links] == ["open", "revoked"]
    assert change_request.events[-1].action == "share"
    assert change_request.events[-1].to_status == "explained"
    assert "作り直した" in change_request.events[-1].note
    assert first.token != second.token
    assert ACTION_LABEL["share"] == "共有リンクを作った"


async def test_create_refuses_after_decision(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    await ChangeRequestService(db_session).explain(project, 1, actor_id=None)
    await ChangeRequestService(db_session).decide(
        project,
        1,
        ChangeRequestDecision(decision="agreed", decided_at=_AT, reason="合う"),
        actor_id=None,
    )

    with pytest.raises(ChangeRequestInvalidTransitionError):
        await _share(db_session, project)


async def test_revoke_is_idempotent_and_extend(db_session: AsyncSession) -> None:
    """取り消しは1度だけ記録し、延長は今からの日数に置き直す。取り消したリンクは延ばせない。"""
    project = await _project(db_session)
    created = await _share(db_session, project, days=1)
    service = ShareLinkService(db_session)
    link_id = created.view.link.id

    extended = await service.extend(
        project, 1, link_id, ShareLinkExtend(expires_in_days=60), actor_id=None
    )
    revoked = await service.revoke(project, 1, link_id, actor_id=None)
    again = await service.revoke(project, 1, link_id, actor_id=None)

    change_request = await ChangeRequestService(db_session).load(project, 1)
    actions = [e.action for e in change_request.events]
    assert extended.link.expires_at.replace(tzinfo=UTC) - datetime.now(UTC) > timedelta(days=59)
    assert (revoked.state, again.state) == ("revoked", "revoked")
    assert actions[-2:] == ["extend_share", "unshare"]
    assert ACTION_LABEL["unshare"] == "共有リンクを取り消した"
    with pytest.raises(ShareLinkNotExtendableError):
        await service.extend(
            project, 1, link_id, ShareLinkExtend(expires_in_days=7), actor_id=None
        )


async def test_link_of_another_change_request_is_not_found(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    created = await _share(db_session, project)
    await ChangeRequestService(db_session).create(project, _CREATE, created_by=None)

    with pytest.raises(ShareLinkNotFoundError):
        await ShareLinkService(db_session).revoke(project, 2, created.view.link.id, actor_id=None)


def test_expires_in_days_is_bounded() -> None:
    assert ShareLinkCreate().expires_in_days == 14
    with pytest.raises(ValidationError):
        ShareLinkCreate(expires_in_days=0)
    with pytest.raises(ValidationError):
        ShareLinkExtend(expires_in_days=91)


async def test_repository_keeps_only_hash(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    created = await _share(db_session, project)

    repository = ChangeRequestShareLinkRepository(db_session)
    rows = await repository.list_for(created.view.link.change_request_id)
    assert isinstance(rows[0], ChangeRequestShareLink)
    assert rows[0].token_hash != created.token
    assert await repository.active_for(rows[0].change_request_id) is rows[0]


# --- 103-3: ログインの要らない閲覧 ---


async def test_view_open_link_shows_explanation_and_records_view(
    db_session: AsyncSession,
) -> None:
    project = await _project(db_session)
    created = await _share(db_session, project)

    view = await ShareLinkService(db_session).view(created.token)

    assert view.state == "open"
    assert view.explanation is not None
    assert view.explanation.output.startswith("# 変更の説明書 CR-1")
    assert view.changes == () and view.changed_since is None
    assert view.link.last_viewed_at is not None


async def test_view_pauses_while_editing_and_shows_changes_after_explaining_again(
    db_session: AsyncSession,
) -> None:
    """直して下書きに戻ると説明書を見せず、もう一度説明済みにすると前回から変わった節を出す。"""
    project = await _project(db_session)
    created = await _share(db_session, project)
    service = ChangeRequestService(db_session)
    await service.update(
        project, 1, ChangeRequestUpdate(content="予約の登録と変更の前に確認を挟む"), actor_id=None
    )

    paused = await ShareLinkService(db_session).view(created.token)
    await service.explain(project, 1, actor_id=None)
    reopened = await ShareLinkService(db_session).view(created.token)

    assert (paused.state, paused.explanation) == ("paused", None)
    assert reopened.state == "open"
    assert [c.name for c in reopened.changes] == ["変更の内容と背景"]
    assert reopened.changes[0].removed == ("予約の登録の前に確認を挟む",)
    assert reopened.changes[0].added == ("予約の登録と変更の前に確認を挟む",)
    assert reopened.changed_since is not None


async def test_view_after_decision_and_withdraw(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    created = await _share(db_session, project)
    service = ChangeRequestService(db_session)
    await service.decide(
        project,
        1,
        ChangeRequestDecision(decision="rejected", decided_at=_AT, reason="範囲の外"),
        actor_id=None,
    )

    decided = await ShareLinkService(db_session).view(created.token)

    assert decided.state == "decided"
    assert decided.explanation is not None and decided.changes == ()

    await service.create(project, _CREATE, created_by=None)
    second = await ShareLinkService(db_session).create(
        project, 2, ShareLinkCreate(), actor_id=None
    )
    await service.withdraw(project, 2, ChangeRequestWithdraw(reason="出し直さない"), actor_id=None)
    closed = await ShareLinkService(db_session).view(second.token)
    assert (closed.state, closed.explanation) == ("closed", None)


async def test_view_errors(db_session: AsyncSession) -> None:
    """無いトークンは404、取り消しと期限切れは410(区別する)、簡易モードのプロジェクトは404。"""
    project = await _project(db_session)
    created = await _share(db_session, project)
    service = ShareLinkService(db_session)

    with pytest.raises(ShareLinkNotFoundError):
        await service.view("unknown-token")

    created.view.link.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    with pytest.raises(ShareLinkExpiredError) as expired:
        await service.view(created.token)
    assert isinstance(expired.value, GoneError)

    await service.revoke(project, 1, created.view.link.id, actor_id=None)
    with pytest.raises(ShareLinkRevokedError):
        await service.view(created.token)

    second = await _share(db_session, project)
    project.mode = "simple"
    await db_session.commit()
    with pytest.raises(ShareLinkNotFoundError):
        await service.view(second.token)


async def test_share_rate_limiter_counts_per_ip(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SHARE_RATE_LIMIT_PER_IP_PER_HOUR", 2)
    limiter = ShareRateLimiter(cast(Redis, FakeRedis()))

    await limiter.enforce_view(ip="10.0.0.1")
    await limiter.enforce_view(ip="10.0.0.1")
    await limiter.enforce_view(ip="10.0.0.2")
    with pytest.raises(RateLimitExceededError):
        await limiter.enforce_view(ip="10.0.0.1")



# --- 104-2: コメント欄 ---


async def _fingerprint(session: AsyncSession, project: Project) -> str:
    service = ChangeRequestService(session)
    change_request = await service.load(project, 1)
    return (await service.explanation_of(change_request)).explanation.fingerprint


async def test_client_comment_and_developer_reply(db_session: AsyncSession) -> None:
    """クライアントのコメントに開発者が返信し、閲覧の応答と開発者の一覧の両方に出る。"""
    project = await _project(db_session)
    created = await _share(db_session, project)
    fingerprint = await _fingerprint(db_session, project)
    service = ShareLinkService(db_session)

    thread = await service.comment(
        created.token,
        SharedCommentCreate(name=" 山田 ", body="確認はどの画面ですか", fingerprint=fingerprint),
    )
    reply = ChangeRequestReplyCreate(body="登録の画面です")
    replied = await service.reply(project, 1, thread.comment.id, reply, actor_id=None)

    assert (thread.comment.author_name, thread.comment.is_developer) == ("山田", False)
    assert thread.comment.fingerprint == fingerprint
    assert [r.body for r in replied.replies] == ["登録の画面です"]
    assert replied.replies[0].is_developer is True
    assert replied.replies[0].fingerprint == fingerprint
    view = await service.view(created.token)
    assert [(t.comment.body, len(t.replies)) for t in view.comments] == [
        ("確認はどの画面ですか", 1)
    ]
    listed = await service.comments_for(project, 1)
    assert listed == list(view.comments)
    change_request = await ChangeRequestService(db_session).load(project, 1)
    actions = [(e.action, e.note, e.to_status) for e in change_request.events[-2:]]
    assert actions == [("comment", "山田", "explained"), ("reply_comment", "", "explained")]
    assert change_request.events[-2].refs == {"comment_id": str(thread.comment.id)}
    assert (ACTION_LABEL["comment"], ACTION_LABEL["reply_comment"]) == (
        "コメントを受けた",
        "コメントに返信した",
    )
    rows = await ChangeRequestCommentRepository(db_session).list_for(change_request.id)
    assert isinstance(rows[0], ChangeRequestComment)
    assert rows[0].share_link_id == created.view.link.id
    assert comment_facts(rows[1]).parent_id == rows[0].id


async def test_comment_refused_when_not_open_or_version_changed(db_session: AsyncSession) -> None:
    """直している間は書き込めず(409)、説明し直した後は古い版の指紋で書き込めない(409)。"""
    project = await _project(db_session)
    created = await _share(db_session, project)
    old = await _fingerprint(db_session, project)
    service = ShareLinkService(db_session)
    cr_service = ChangeRequestService(db_session)
    await cr_service.update(
        project, 1, ChangeRequestUpdate(content="予約の登録と変更の前に確認を挟む"), actor_id=None
    )

    with pytest.raises(ShareLinkNotWritableError):
        await service.comment(
            created.token, SharedCommentCreate(name="山田", body="質問", fingerprint=old)
        )
    await cr_service.explain(project, 1, actor_id=None)
    with pytest.raises(ShareExplanationChangedError):
        await service.comment(
            created.token, SharedCommentCreate(name="山田", body="質問", fingerprint=old)
        )
    new = await _fingerprint(db_session, project)
    await service.comment(
        created.token, SharedCommentCreate(name="山田", body="質問", fingerprint=new)
    )
    await service.revoke(project, 1, created.view.link.id, actor_id=None)
    with pytest.raises(ShareLinkRevokedError):
        await service.comment(
            created.token, SharedCommentCreate(name="山田", body="質問", fingerprint=new)
        )


async def test_reply_rules_and_comments_survive_recreate(db_session: AsyncSession) -> None:
    """返信への返信は409、無いコメントは404。リンクを作り直してもコメントは残る。"""
    project = await _project(db_session)
    created = await _share(db_session, project)
    fingerprint = await _fingerprint(db_session, project)
    service = ShareLinkService(db_session)
    thread = await service.comment(
        created.token, SharedCommentCreate(name="山田", body="質問", fingerprint=fingerprint)
    )
    replied = await service.reply(
        project, 1, thread.comment.id, ChangeRequestReplyCreate(body="答え"), actor_id=None
    )

    with pytest.raises(ChangeRequestCommentNotRepliableError):
        await service.reply(
            project, 1, replied.replies[0].id, ChangeRequestReplyCreate(body="再"), actor_id=None
        )
    with pytest.raises(ChangeRequestCommentNotFoundError):
        await service.reply(
            project, 1, created.view.link.id, ChangeRequestReplyCreate(body="再"), actor_id=None
        )
    second = await _share(db_session, project)
    view = await service.view(second.token)
    assert [t.comment.body for t in view.comments] == ["質問"]


def test_comment_schemas_validate() -> None:
    """名前・本文は前後の空白を除いて空でなく、指紋は sha256 の形。返信の本文も空でない。"""
    with pytest.raises(ValidationError):
        SharedCommentCreate(name=" ", body="質問", fingerprint="f" * 64)
    with pytest.raises(ValidationError):
        SharedCommentCreate(name="山田", body="質問", fingerprint="xyz")
    with pytest.raises(ValidationError):
        ChangeRequestReplyCreate(body="  ")


async def test_share_write_rate_limit_is_separate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SHARE_WRITE_RATE_LIMIT_PER_IP_PER_HOUR", 1)
    limiter = ShareRateLimiter(cast(Redis, FakeRedis()))

    await limiter.enforce_write(ip="10.0.0.1")
    await limiter.enforce_view(ip="10.0.0.1")  # 閲覧は別に数える
    with pytest.raises(RateLimitExceededError):
        await limiter.enforce_write(ip="10.0.0.1")



# --- 104-3: 共有リンクからの合意・却下 ---


async def test_share_decision_records_name_source_and_fingerprint(
    db_session: AsyncSession,
) -> None:
    """共有リンクの合意は CR の判断になり、相手 = 書いた名前・出どころ・見た版を残す
    (理由は任意)。"""
    project = await _project(db_session)
    created = await _share(db_session, project)
    fingerprint = await _fingerprint(db_session, project)

    view = await ShareLinkService(db_session).decide(
        created.token,
        SharedDecisionCreate(decision="agreed", name=" 山田 ", fingerprint=fingerprint),
    )

    change_request = view.change_request
    assert (view.state, change_request.status) == ("decided", "agreed")
    assert change_request.decision_counterpart == "山田"
    assert change_request.decision_reason == "共有リンクで合意"
    assert change_request.decision_source == "share_link"
    assert change_request.decision_fingerprint == fingerprint
    assert change_request.decision_recorded_by is None
    assert change_request.predicted_impact is not None
    event = change_request.events[-1]
    assert (event.action, event.actor_id) == ("agree", None)
    assert event.refs == {
        "source": "share_link",
        "fingerprint": fingerprint,
        "link_id": str(created.view.link.id),
    }
    trace = await ChangeRequestService(db_session).trace(project, 1)
    assert trace.entries[-1].note == "出どころ: 共有リンク / 相手: 山田 / 理由: 共有リンクで合意"


async def test_share_reject_needs_reason(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    created = await _share(db_session, project)
    fingerprint = await _fingerprint(db_session, project)
    service = ShareLinkService(db_session)

    with pytest.raises(ShareDecisionReasonRequiredError):
        await service.decide(
            created.token,
            SharedDecisionCreate(
                decision="rejected", name="山田", fingerprint=fingerprint, reason=" "
            ),
        )
    view = await service.decide(
        created.token,
        SharedDecisionCreate(
            decision="rejected", name="山田", fingerprint=fingerprint, reason="予算が合わない"
        ),
    )
    assert view.change_request.status == "rejected"
    assert view.change_request.decision_reason == "予算が合わない"


async def test_first_decision_wins_and_stale_version_is_refused(db_session: AsyncSession) -> None:
    """先に開発者が判断するとリンクは書き込めない。開発者の判断も出どころと今の版を残す。2度目の
    リンクの判断・古い版の判断も断る。"""
    project = await _project(db_session)
    created = await _share(db_session, project)
    fingerprint = await _fingerprint(db_session, project)
    service = ShareLinkService(db_session)
    cr_service = ChangeRequestService(db_session)

    with pytest.raises(ShareExplanationChangedError):
        await service.decide(
            created.token,
            SharedDecisionCreate(decision="agreed", name="山田", fingerprint="0" * 64),
        )
    await cr_service.decide(
        project,
        1,
        ChangeRequestDecision(decision="agreed", decided_at=_AT, reason="打ち合わせで合意"),
        actor_id=None,
    )
    change_request = await cr_service.load(project, 1)
    assert change_request.decision_source == "developer"
    assert change_request.decision_fingerprint == fingerprint
    with pytest.raises(ShareLinkNotWritableError):
        await service.decide(
            created.token,
            SharedDecisionCreate(
                decision="rejected", name="山田", fingerprint=fingerprint, reason="x"
            ),
        )
