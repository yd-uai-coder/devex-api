import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.change_requests import (
    READABLE_STATES,
    CommentFacts,
    CommentThread,
    Freshness,
    SectionChange,
    ShareState,
    aware,
    can_reply,
    comment_threads,
    expires_at_from,
    explained_snapshots,
    explanation_changes,
    new_token,
    next_status,
    previous_snapshot,
    share_decision_reason,
    share_state,
    token_hash,
    write_problem,
)
from app.models.change_request import ChangeRequest
from app.models.change_request_comment import ChangeRequestComment
from app.models.change_request_share_link import ChangeRequestShareLink
from app.models.project import Project
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.change_request_comment import ChangeRequestCommentRepository
from app.repositories.change_request_share_link import ChangeRequestShareLinkRepository
from app.repositories.project import ProjectRepository
from app.schemas.change_request import ChangeRequestDecision
from app.schemas.share_link import (
    ChangeRequestReplyCreate,
    SharedCommentCreate,
    SharedDecisionCreate,
    ShareLinkCreate,
    ShareLinkExtend,
)
from app.services.change_request_service import ChangeRequestService, ExplanationView
from app.services.errors import (
    ChangeRequestCommentNotFoundError,
    ChangeRequestCommentNotRepliableError,
    ChangeRequestInvalidTransitionError,
    ShareDecisionReasonRequiredError,
    ShareExplanationChangedError,
    ShareLinkExpiredError,
    ShareLinkNotExtendableError,
    ShareLinkNotFoundError,
    ShareLinkNotWritableError,
    ShareLinkRevokedError,
)

logger = structlog.get_logger(__name__)

# 共有リンクを作れる CR の状態(下書きは作ると説明済みになる)
SHAREABLE_STATUSES = frozenset({"draft", "explained"})
# コメント欄の書き手の種類(クライアント・開発者)
CLIENT_AUTHOR = "client"
DEVELOPER_AUTHOR = "developer"


@dataclass(frozen=True)
class ShareLinkView:
    """共有リンク1本と、今の状態(取り消し・期限と CR の状態から決めたもの)。"""

    link: ChangeRequestShareLink
    state: ShareState


@dataclass(frozen=True)
class CreatedShareLink:
    """作った共有リンクと、1度だけ返すトークン。`summary_needs_reconfirm`は、確認済みの要約が
    その後の説明書の変化で古くなっているか(共有の前に確かめ直すよう知らせる。作成は止めない)。
    `baseline_freshness`は説明書の規模を数えた基準の束の鮮度(古ければ知らせる。作成は止めない)。"""

    view: ShareLinkView
    token: str
    summary_needs_reconfirm: bool
    baseline_freshness: Freshness | None = None


@dataclass(frozen=True)
class SharedView:
    """公開の入口で見せる CR。`explanation`は説明書を見せる状態(開いている・判断済み)だけ。
    `changes`は前回説明済みにした時点からの変わった節(開いているときだけ)、`changed_since`はその
    前回の日時。`comments`はコメント欄(説明書を見せる状態だけ)。"""

    change_request: ChangeRequest
    link: ChangeRequestShareLink
    state: ShareState
    explanation: ExplanationView | None
    changes: tuple[SectionChange, ...]
    changed_since: datetime | None
    comments: tuple[CommentThread, ...] = ()


class ShareLinkService:
    """CR の共有リンクの作成・一覧・取り消し・延長(開発者)と、ログインの要らない閲覧・コメント・
    合意 / 却下、コメント欄の一覧・返信(開発者)(docs/external_design.md 2.16節)。

    CR に有効なリンクは1本で、作り直すと前のリンクを取り消す。下書きの CR にリンクを作ると説明済みに
    する(説明書の写しを時系列に残す)。リンクの状態は保存せず、取り消し・期限と CR の状態から決める
    (app/change_requests/sharing.py)。詳細設計モードだけ。回数の上限は公開の入口のルートで数える。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._links = ChangeRequestShareLinkRepository(session)
        self._change_requests = ChangeRequestRepository(session)
        self._comments = ChangeRequestCommentRepository(session)
        self._service = ChangeRequestService(session)

    async def create(
        self,
        project: Project,
        number: int,
        payload: ShareLinkCreate,
        *,
        actor_id: uuid.UUID | None,
    ) -> CreatedShareLink:
        """CR の共有リンクを作る(下書き・説明済みだけ。ほかは ChangeRequestInvalidTransitionError)。
        下書きなら説明済みにし、説明済みなら記録だけの行を足す。有効なリンクがあれば取り消してから
        作る。トークンは応答で1度だけ返す。"""
        change_request = await self._service.load(project, number)
        if change_request.status not in SHAREABLE_STATUSES:
            raise ChangeRequestInvalidTransitionError(
                f"CR-{number} cannot be shared (status={change_request.status})"
            )
        previous = await self._links.active_for(change_request.id)
        note = "共有リンクを作った"
        if previous is not None:
            await self._links.revoke(previous, revoked_by=actor_id)
            note = "共有リンクを作り直した(前のリンクは取り消した)"
        if next_status(change_request.status, "explain") is not None:
            await self._service.record_explain(change_request, actor_id=actor_id, note=note)
        else:
            await self._change_requests.add_event(
                change_request,
                action="share",
                to_status=change_request.status,
                actor_id=actor_id,
                note=note,
            )
        token = new_token()
        now = datetime.now(UTC)
        link = await self._links.create(
            change_request_id=change_request.id,
            token_hash=token_hash(token),
            expires_at=expires_at_from(now, payload.expires_in_days),
            created_by=actor_id,
        )
        await self._session.commit()
        logger.info(
            "change_request_share_link_created", project_id=str(project.id), number=number
        )
        explanation = await self._service.explanation_of(change_request)
        return CreatedShareLink(
            view=self._view(link, change_request),
            token=token,
            summary_needs_reconfirm=explanation.summary_needs_reconfirm,
            baseline_freshness=explanation.baseline_freshness,
        )

    async def list_for(self, project: Project, number: int) -> list[ShareLinkView]:
        """CR の共有リンク(新しい順。取り消したものも)。"""
        change_request = await self._service.load(project, number)
        links = await self._links.list_for(change_request.id)
        return [self._view(link, change_request) for link in links]

    async def revoke(
        self, project: Project, number: int, link_id: uuid.UUID, *, actor_id: uuid.UUID | None
    ) -> ShareLinkView:
        """共有リンクを取り消す(行は残し、時系列に記録だけの行を足す)。取り消し済みなら何もしない。"""
        change_request, link = await self._load_link(project, number, link_id)
        if link.revoked_at is None:
            await self._links.revoke(link, revoked_by=actor_id)
            await self._change_requests.add_event(
                change_request,
                action="unshare",
                to_status=change_request.status,
                actor_id=actor_id,
            )
            await self._session.commit()
        return self._view(link, change_request)

    async def extend(
        self,
        project: Project,
        number: int,
        link_id: uuid.UUID,
        payload: ShareLinkExtend,
        *,
        actor_id: uuid.UUID | None,
    ) -> ShareLinkView:
        """共有リンクの期限を、今から`expires_in_days`日後に置き直す(期限切れのリンクも開き直せる)。
        取り消したリンクは ShareLinkNotExtendableError。"""
        change_request, link = await self._load_link(project, number, link_id)
        if link.revoked_at is not None:
            raise ShareLinkNotExtendableError(
                "取り消した共有リンクは延ばせません。作り直してください。"
            )
        link.expires_at = expires_at_from(datetime.now(UTC), payload.expires_in_days)
        await self._change_requests.add_event(
            change_request,
            action="extend_share",
            to_status=change_request.status,
            actor_id=actor_id,
            note=f"期限を {link.expires_at:%Y-%m-%d} までにした",
        )
        await self._session.commit()
        return self._view(link, change_request)

    async def view(self, token: str) -> SharedView:
        """トークンの共有リンクで見せる CR(ログインの要らない入口)。リンクが無い・簡易ドキュメント
        モードのプロジェクトは ShareLinkNotFoundError、取り消しは ShareLinkRevokedError、期限切れは
        ShareLinkExpiredError。読めたら最後に見られた日時を残す。直している・閉じた CR は説明書を
        見せない。"""
        link, change_request, state = await self._resolve(token)
        link.last_viewed_at = datetime.now(UTC)
        await self._session.commit()
        explanation = None
        changes: tuple[SectionChange, ...] = ()
        changed_since = None
        comments: tuple[CommentThread, ...] = ()
        if state in READABLE_STATES:
            explanation = await self._service.explanation_of(change_request)
            comments = tuple(await self._threads(change_request))
        if state == "open" and explanation is not None:
            previous = previous_snapshot(
                explained_snapshots(
                    (e.action, e.refs, aware(e.occurred_at)) for e in change_request.events
                )
            )
            if previous is not None:
                changes = explanation_changes(
                    previous.markdown, explanation.explanation.markdown
                )
                changed_since = previous.explained_at
        return SharedView(
            change_request, link, state, explanation, changes, changed_since, comments
        )

    async def comment(self, token: str, payload: SharedCommentCreate) -> CommentThread:
        """クライアントが共有リンクからコメントする(開いているリンクで、見た版が今の版のときだけ)。
        時系列に記録だけの行を足す。"""
        link, change_request, state = await self._resolve(token)
        await self._require_writable(change_request, state, payload.fingerprint)
        comment = await self._comments.create(
            change_request_id=change_request.id,
            author_kind=CLIENT_AUTHOR,
            author_name=payload.name,
            body=payload.body,
            fingerprint=payload.fingerprint,
            share_link_id=link.id,
        )
        await self._change_requests.add_event(
            change_request,
            action="comment",
            to_status=change_request.status,
            actor_id=None,
            note=payload.name,
            refs={"comment_id": str(comment.id)},
        )
        await self._session.commit()
        return CommentThread(comment_facts(comment), ())

    async def decide(self, token: str, payload: SharedDecisionCreate) -> SharedView:
        """クライアントが共有リンクで合意・却下する(開いているリンクで、見た版が今の版のときだけ)。
        開発者の合意と同じ規則(案の選び方・予測の保存・選んだ案の対象)を通し、相手 = 書いた名前・
        出どころ = 共有リンク・見た説明書の指紋を残す。先に開発者が判断していれば、リンクは開いて
        いないので書き込めない。判断の後の閲覧の形を返す。"""
        link, change_request, state = await self._resolve(token)
        await self._require_writable(change_request, state, payload.fingerprint)
        reason = share_decision_reason(payload.decision, payload.reason)
        if reason is None:
            raise ShareDecisionReasonRequiredError("却下の理由を書いてください。")
        decision = ChangeRequestDecision(
            decision=payload.decision,
            decided_at=datetime.now(UTC),
            option=payload.option,
            counterpart=payload.name,
            reason=reason,
        )
        await self._service.record_decision(
            change_request,
            decision,
            actor_id=None,
            source="share_link",
            fingerprint=payload.fingerprint,
            refs={
                "source": "share_link",
                "fingerprint": payload.fingerprint,
                "link_id": str(link.id),
            },
        )
        await self._session.commit()
        logger.info(
            "change_request_decided_via_share_link",
            project_id=str(change_request.project_id),
            number=change_request.number,
            decision=payload.decision,
        )
        return await self.view(token)

    async def comments_for(self, project: Project, number: int) -> list[CommentThread]:
        """CR のコメント欄(開発者。コメントごとに返信をまとめ、古い順)。"""
        change_request = await self._service.load(project, number)
        return await self._threads(change_request)

    async def reply(
        self,
        project: Project,
        number: int,
        comment_id: uuid.UUID,
        payload: ChangeRequestReplyCreate,
        *,
        actor_id: uuid.UUID | None,
    ) -> CommentThread:
        """開発者がクライアントのコメントに返信する(CR の状態によらない。返信には返信しない)。
        返信した時点の説明書の指紋を残す。返したコメントと、その返信の一覧を返す。"""
        change_request = await self._service.load(project, number)
        parent = await self._comments.find_one(
            id=comment_id, change_request_id=change_request.id
        )
        if parent is None:
            raise ChangeRequestCommentNotFoundError(
                f"Comment {comment_id} not found in CR-{number}"
            )
        if not can_reply(comment_facts(parent)):
            raise ChangeRequestCommentNotRepliableError("返信には返信できません。")
        explanation = await self._service.explanation_of(change_request)
        reply = await self._comments.create(
            change_request_id=change_request.id,
            author_kind=DEVELOPER_AUTHOR,
            author_id=actor_id,
            body=payload.body,
            fingerprint=explanation.explanation.fingerprint,
            parent_id=parent.id,
        )
        await self._change_requests.add_event(
            change_request,
            action="reply_comment",
            to_status=change_request.status,
            actor_id=actor_id,
            refs={"comment_id": str(reply.id), "parent_id": str(parent.id)},
        )
        await self._session.commit()
        threads = await self._threads(change_request)
        return next(t for t in threads if t.comment.id == parent.id)

    async def _resolve(
        self, token: str
    ) -> tuple[ChangeRequestShareLink, ChangeRequest, ShareState]:
        """トークンのリンクと CR と、リンクの今の状態(公開の入口の共通)。リンクが無い・簡易
        ドキュメントモードは ShareLinkNotFoundError、取り消しは ShareLinkRevokedError、期限切れは
        ShareLinkExpiredError。"""
        link = await self._links.get_by_hash(token_hash(token))
        if link is None:
            raise ShareLinkNotFoundError("Share link not found")
        # 時系列・束も一緒に読む(get_by_id は既定の読み込みを付けないため)
        change_request = await self._change_requests.find_one(id=link.change_request_id)
        assert change_request is not None  # リンクは CR と一緒に消える
        project = await ProjectRepository(self._session).find_one(
            id=change_request.project_id, deleted_at=None
        )
        if project is None or project.mode != "detailed":
            raise ShareLinkNotFoundError("Share link not found")
        state = self._state(link, change_request)
        if state == "revoked":
            raise ShareLinkRevokedError(
                "このリンクは使えなくなりました。開発者に今のリンクを確かめてください。"
            )
        if state == "expired":
            raise ShareLinkExpiredError(
                "このリンクは期限が切れました。開発者に新しいリンクか延長を頼んでください。"
            )
        return link, change_request, state

    async def _require_writable(
        self, change_request: ChangeRequest, state: ShareState, seen_fingerprint: str
    ) -> ExplanationView:
        """共有リンクから書き込めるかを確かめ、今の説明書を返す。開いていなければ
        ShareLinkNotWritableError、見た版が今の版と違えば ShareExplanationChangedError。"""
        explanation = await self._service.explanation_of(change_request)
        problem = write_problem(state, seen_fingerprint, explanation.explanation.fingerprint)
        if problem == "not_writable":
            raise ShareLinkNotWritableError(
                "この変更は今、書き込みを受け付けていません(直している・判断済みなど)。"
            )
        if problem == "changed":
            raise ShareExplanationChangedError(
                "説明書が新しくなりました。読み直してから、もう一度送ってください。"
            )
        return explanation

    async def _threads(self, change_request: ChangeRequest) -> list[CommentThread]:
        rows = await self._comments.list_for(change_request.id)
        return comment_threads(comment_facts(row) for row in rows)

    async def _load_link(
        self, project: Project, number: int, link_id: uuid.UUID
    ) -> tuple[ChangeRequest, ChangeRequestShareLink]:
        change_request = await self._service.load(project, number)
        link = await self._links.find_one(id=link_id, change_request_id=change_request.id)
        if link is None:
            raise ShareLinkNotFoundError(f"Share link {link_id} not found in CR-{number}")
        return change_request, link

    def _view(self, link: ChangeRequestShareLink, change_request: ChangeRequest) -> ShareLinkView:
        return ShareLinkView(link, self._state(link, change_request))

    @staticmethod
    def _state(link: ChangeRequestShareLink, change_request: ChangeRequest) -> ShareState:
        return share_state(
            link.revoked_at,
            aware(link.expires_at),
            change_request.status,
            datetime.now(UTC),
        )


def comment_facts(comment: ChangeRequestComment) -> CommentFacts:
    """コメントの行を、並べ方の規則(comments.py)が読む値に写す。"""
    return CommentFacts(
        id=comment.id,
        parent_id=comment.parent_id,
        author_name=comment.author_name,
        is_developer=comment.author_kind == DEVELOPER_AUTHOR,
        body=comment.body,
        fingerprint=comment.fingerprint,
        created_at=aware(comment.created_at),
        author_id=comment.author_id,
    )
