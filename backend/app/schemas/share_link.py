"""CR の共有リンクのスキーマ(開発者の作成・一覧・延長と、ログインの要らない閲覧の応答・コメント、
開発者のコメント欄の一覧・返信)。

docs/external_design.md 2.16節「共有リンク」。公開の閲覧の応答には、その CR の説明書・選択肢・
判断の結果・コメント欄だけを入れ、プロジェクト・利用者などの内部の ID は出さない(開発者の返信の
書き手は「開発者」)。
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.change_requests import (
    COMMENT_BODY_MAX,
    DEFAULT_EXPIRES_DAYS,
    DEVELOPER_LABEL,
    MAX_EXPIRES_DAYS,
    SHARE_STATE_LABEL,
    STATUS_LABEL,
    CommentFacts,
    CommentThread,
    SectionChange,
    ShareState,
    aware,
    decision_source_label,
    option_label,
    options_from_json,
)
from app.schemas.agreement import COUNTERPART_MAX, REASON_MAX

if TYPE_CHECKING:
    from app.models.change_request_share_link import ChangeRequestShareLink
    from app.services.share_link_service import CreatedShareLink, SharedView, ShareLinkView

# クライアントの画面のパス(トークンを末尾に付ける。画面は Phase 108)
SHARE_PATH_PREFIX = "/share/"

# クライアントが書く名前(合意の記録の相手と同じ長さ)・コメントの本文・見た説明書の版(sha256)
ClientName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=COUNTERPART_MAX)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=COMMENT_BODY_MAX)
]
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ShareLinkCreate(BaseModel):
    """共有リンクの作成。期限は今からの日数(1〜90日。既定14日)。"""

    expires_in_days: int = Field(default=DEFAULT_EXPIRES_DAYS, ge=1, le=MAX_EXPIRES_DAYS)


class ShareLinkExtend(BaseModel):
    """共有リンクの期限の延長(今からの日数に置き直す)。"""

    expires_in_days: int = Field(default=DEFAULT_EXPIRES_DAYS, ge=1, le=MAX_EXPIRES_DAYS)


class ShareLinkRead(BaseModel):
    """共有リンク1本(トークンは含めない)。`state`は今の状態(開いている・直している・判断済み・
    閉じた・期限切れ・取り消し)。"""

    id: uuid.UUID
    state: ShareState
    state_label: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_viewed_at: datetime | None

    @classmethod
    def build(cls, view: "ShareLinkView") -> "ShareLinkRead":
        return cls(
            **_link_fields(view.link),
            state=view.state,
            state_label=SHARE_STATE_LABEL[view.state],
        )


class ShareLinkCreatedRead(ShareLinkRead):
    """作った共有リンク。`token`と`path`(クライアントの画面のパス)はこの応答でだけ返す。
    `summary_needs_reconfirm`は、確認済みの要約がその後の説明書の変化で古くなっているか。"""

    token: str
    path: str
    summary_needs_reconfirm: bool

    @classmethod
    def from_created(cls, created: "CreatedShareLink") -> "ShareLinkCreatedRead":
        view = created.view
        return cls(
            **_link_fields(view.link),
            state=view.state,
            state_label=SHARE_STATE_LABEL[view.state],
            token=created.token,
            path=f"{SHARE_PATH_PREFIX}{created.token}",
            summary_needs_reconfirm=created.summary_needs_reconfirm,
        )


class SectionChangeRead(BaseModel):
    """前回説明した時点から変わった節1つ(節の名前・消えた行・足した行)。"""

    name: str
    removed: list[str]
    added: list[str]

    @classmethod
    def from_change(cls, change: SectionChange) -> "SectionChangeRead":
        return cls(name=change.name, removed=list(change.removed), added=list(change.added))


class SharedOptionRead(BaseModel):
    """クライアントが選ぶ案1つ(番号・呼び名・名前・結果・おすすめか)。変える要件と規模は説明書に。"""

    number: int
    label: str
    name: str
    outcome: str
    recommended: bool


class SharedDecisionRead(BaseModel):
    """判断の結果(合意 / 却下・日時・相手・選んだ案の呼び名と名前)。`source`は出どころ
    (developer / share_link。出どころを残す前の判断は null)。"""

    decision: Literal["agreed", "rejected"]
    decision_label: str
    decided_at: datetime
    counterpart: str | None
    option: str | None
    source: str | None = None
    source_label: str | None = None


class SharedDecisionCreate(BaseModel):
    """共有リンクからの合意・却下(名前・見た説明書の版と、案のある CR の合意では選んだ案の番号)。
    理由は却下では必須(空白だけも不可。サービスで確かめる)、合意では任意。"""

    decision: Literal["agreed", "rejected"]
    name: ClientName
    fingerprint: Fingerprint
    option: int | None = Field(default=None, ge=1)
    reason: str = Field(default="", max_length=REASON_MAX)


class SharedCommentCreate(BaseModel):
    """共有リンクからのコメント(名前・本文と、見た説明書の版)。"""

    name: ClientName
    body: CommentBody
    fingerprint: Fingerprint


class SharedCommentRead(BaseModel):
    """クライアントに見せるコメント1つ(書き手・本文・日時。開発者の返信の書き手は「開発者」)。
    `replies`はコメントへの返信(古い順。返信では空)。"""

    author: str
    from_developer: bool
    body: str
    created_at: datetime
    replies: list["SharedCommentRead"] = []

    @classmethod
    def from_facts(cls, facts: CommentFacts) -> "SharedCommentRead":
        return cls(
            author=DEVELOPER_LABEL if facts.is_developer else facts.author_name,
            from_developer=facts.is_developer,
            body=facts.body,
            created_at=facts.created_at,
        )

    @classmethod
    def from_thread(cls, thread: CommentThread) -> "SharedCommentRead":
        read = cls.from_facts(thread.comment)
        read.replies = [cls.from_facts(reply) for reply in thread.replies]
        return read


class ChangeRequestReplyCreate(BaseModel):
    """開発者の返信(本文)。"""

    body: CommentBody


class ChangeRequestCommentRead(BaseModel):
    """開発者に見せるコメント1つ。`author_name`はクライアントが書いた名前(返信は空)、`author_id`は
    返信した利用者、`fingerprint`は書いたときに見ていた説明書の版。`can_reply`は返信できるか、
    `replies`はコメントへの返信(古い順)。"""

    id: uuid.UUID
    from_developer: bool
    author_name: str
    author_id: uuid.UUID | None
    body: str
    fingerprint: str
    created_at: datetime
    can_reply: bool
    replies: list["ChangeRequestCommentRead"] = []

    @classmethod
    def from_facts(cls, facts: CommentFacts, *, can_reply: bool) -> "ChangeRequestCommentRead":
        return cls(
            id=facts.id,
            from_developer=facts.is_developer,
            author_name=facts.author_name,
            author_id=facts.author_id,
            body=facts.body,
            fingerprint=facts.fingerprint,
            created_at=facts.created_at,
            can_reply=can_reply,
        )

    @classmethod
    def from_thread(cls, thread: CommentThread) -> "ChangeRequestCommentRead":
        read = cls.from_facts(thread.comment, can_reply=not thread.comment.is_developer)
        read.replies = [cls.from_facts(reply, can_reply=False) for reply in thread.replies]
        return read


class SharedChangeRequestRead(BaseModel):
    """共有リンクで見せる CR。`markdown`(出力する説明書。確認済みで古くない要約つき)・
    `fingerprint`(見た説明書の版)・`options`は、開いている・判断済みのときだけ。`changes`は
    開いているときの前回からの変わった節(`changed_since`は前回説明した日時)。`comments`は
    コメント欄(開いている・判断済みのときだけ)。"""

    state: ShareState
    state_label: str
    label: str
    title: str
    expires_at: datetime
    markdown: str | None = None
    fingerprint: str | None = None
    options: list[SharedOptionRead] = []
    recommended_reason: str = ""
    changes: list[SectionChangeRead] = []
    changed_since: datetime | None = None
    decision: SharedDecisionRead | None = None
    comments: list[SharedCommentRead] = []

    @classmethod
    def build(cls, view: "SharedView") -> "SharedChangeRequestRead":
        change_request = view.change_request
        read = cls(
            state=view.state,
            state_label=SHARE_STATE_LABEL[view.state],
            label=f"CR-{change_request.number}",
            title=change_request.title,
            expires_at=aware(view.link.expires_at),
            changes=[SectionChangeRead.from_change(c) for c in view.changes],
            changed_since=view.changed_since,
            comments=[SharedCommentRead.from_thread(t) for t in view.comments],
        )
        if view.explanation is None:
            return read
        options = options_from_json(change_request.options)
        read.markdown = view.explanation.output
        read.fingerprint = view.explanation.explanation.fingerprint
        read.options = [
            SharedOptionRead(
                number=number,
                label=option_label(number),
                name=option.name,
                outcome=option.outcome,
                recommended=options.recommended == number,
            )
            for number, option in enumerate(options.items, start=1)
        ]
        read.recommended_reason = options.reason
        if change_request.decided_at is not None and change_request.status != "withdrawn":
            number = change_request.decision_option
            option = None
            if number is not None and 1 <= number <= len(options.items):
                option = f"{option_label(number)} {options.option(number).name}"
            decision: Literal["agreed", "rejected"] = (
                "rejected" if change_request.status == "rejected" else "agreed"
            )
            read.decision = SharedDecisionRead(
                decision=decision,
                decision_label=STATUS_LABEL[decision],
                decided_at=aware(change_request.decided_at),
                counterpart=change_request.decision_counterpart,
                option=option,
                source=change_request.decision_source,
                source_label=decision_source_label(change_request.decision_source),
            )
        return read


def _link_fields(link: "ChangeRequestShareLink") -> dict[str, Any]:
    return {
        "id": link.id,
        "created_at": aware(link.created_at),
        "expires_at": aware(link.expires_at),
        "revoked_at": aware(link.revoked_at) if link.revoked_at else None,
        "last_viewed_at": aware(link.last_viewed_at) if link.last_viewed_at else None,
    }
