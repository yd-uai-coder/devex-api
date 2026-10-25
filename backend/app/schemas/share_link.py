"""CR の共有リンクのスキーマ(開発者の作成・一覧・延長と、ログインの要らない閲覧の応答)。

docs/external_design.md 2.16節「共有リンク」。公開の閲覧の応答には、その CR の説明書・選択肢・
判断の結果だけを入れ、プロジェクト・利用者などの内部の ID は出さない。
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from app.change_requests import (
    DEFAULT_EXPIRES_DAYS,
    MAX_EXPIRES_DAYS,
    SHARE_STATE_LABEL,
    STATUS_LABEL,
    SectionChange,
    ShareState,
    aware,
    option_label,
    options_from_json,
)

if TYPE_CHECKING:
    from app.models.change_request_share_link import ChangeRequestShareLink
    from app.services.share_link_service import CreatedShareLink, SharedView, ShareLinkView

# クライアントの画面のパス(トークンを末尾に付ける。画面は Phase 108)
SHARE_PATH_PREFIX = "/share/"


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
    """判断の結果(合意 / 却下・日時・相手・選んだ案の呼び名と名前)。"""

    decision: Literal["agreed", "rejected"]
    decision_label: str
    decided_at: datetime
    counterpart: str | None
    option: str | None


class SharedChangeRequestRead(BaseModel):
    """共有リンクで見せる CR。`markdown`(出力する説明書。確認済みで古くない要約つき)・
    `fingerprint`(見た説明書の版)・`options`は、開いている・判断済みのときだけ。`changes`は
    開いているときの前回からの変わった節(`changed_since`は前回説明した日時)。"""

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
