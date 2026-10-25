import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.change_requests import (
    READABLE_STATES,
    SectionChange,
    ShareState,
    aware,
    expires_at_from,
    explained_snapshots,
    explanation_changes,
    new_token,
    next_status,
    previous_snapshot,
    share_state,
    token_hash,
)
from app.models.change_request import ChangeRequest
from app.models.change_request_share_link import ChangeRequestShareLink
from app.models.project import Project
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.change_request_share_link import ChangeRequestShareLinkRepository
from app.repositories.project import ProjectRepository
from app.schemas.share_link import ShareLinkCreate, ShareLinkExtend
from app.services.change_request_service import ChangeRequestService, ExplanationView
from app.services.errors import (
    ChangeRequestInvalidTransitionError,
    ShareLinkExpiredError,
    ShareLinkNotExtendableError,
    ShareLinkNotFoundError,
    ShareLinkRevokedError,
)

logger = structlog.get_logger(__name__)

# 共有リンクを作れる CR の状態(下書きは作ると説明済みになる)
SHAREABLE_STATUSES = frozenset({"draft", "explained"})


@dataclass(frozen=True)
class ShareLinkView:
    """共有リンク1本と、今の状態(取り消し・期限と CR の状態から決めたもの)。"""

    link: ChangeRequestShareLink
    state: ShareState


@dataclass(frozen=True)
class CreatedShareLink:
    """作った共有リンクと、1度だけ返すトークン。`summary_needs_reconfirm`は、確認済みの要約が
    その後の説明書の変化で古くなっているか(共有の前に確かめ直すよう知らせる。作成は止めない)。"""

    view: ShareLinkView
    token: str
    summary_needs_reconfirm: bool


@dataclass(frozen=True)
class SharedView:
    """公開の入口で見せる CR。`explanation`は説明書を見せる状態(開いている・判断済み)だけ。
    `changes`は前回説明済みにした時点からの変わった節(開いているときだけ)、`changed_since`はその
    前回の日時。"""

    change_request: ChangeRequest
    link: ChangeRequestShareLink
    state: ShareState
    explanation: ExplanationView | None
    changes: tuple[SectionChange, ...]
    changed_since: datetime | None


class ShareLinkService:
    """CR の共有リンクの作成・一覧・取り消し・延長(開発者)と、ログインの要らない閲覧
    (docs/external_design.md 2.16節)。

    CR に有効なリンクは1本で、作り直すと前のリンクを取り消す。下書きの CR にリンクを作ると説明済みに
    する(説明書の写しを時系列に残す)。リンクの状態は保存せず、取り消し・期限と CR の状態から決める
    (app/change_requests/sharing.py)。詳細設計モードだけ。回数の上限は公開の入口のルートで数える。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._links = ChangeRequestShareLinkRepository(session)
        self._change_requests = ChangeRequestRepository(session)
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
        link.last_viewed_at = datetime.now(UTC)
        await self._session.commit()
        explanation = None
        changes: tuple[SectionChange, ...] = ()
        changed_since = None
        if state in READABLE_STATES:
            explanation = await self._service.explanation_of(change_request)
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
        return SharedView(change_request, link, state, explanation, changes, changed_since)

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
