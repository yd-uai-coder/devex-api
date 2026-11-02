import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ChangeRequestShareLink(Base):
    """CR の共有リンク1本(docs/internal_design.md 3.2節㉓)。

    クライアントがアカウントを作らずに、その CR の説明書を読むためのリンク。`token_hash`は
    トークンの sha256(トークンそのものは持たない。app/change_requests/sharing.py)。CR に有効な
    リンクは1本で、作り直すと前のリンクに`revoked_*`を入れる(行は残す)。期限は`expires_at`。
    リンクの状態(開いている・直している・期限切れ…)は保存せず、取り消し・期限と CR の状態から
    読むときに決める。`last_viewed_at`は公開の入口で最後に読まれた日時。
    """

    __tablename__ = "change_request_share_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
