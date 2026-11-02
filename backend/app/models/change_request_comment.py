import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ChangeRequestComment(Base):
    """CR のコメント欄の1行(docs/internal_design.md 3.2節㉔)。

    クライアントが共有リンクから書いたコメント(`author_kind`=client。`author_name`は書いた名前、
    `share_link_id`は書いたリンク)と、開発者の返信(`author_kind`=developer。`author_id`は返信した
    利用者、`parent_id`は返信先のコメント)。返信は1段で、並べ方と返信できるコメントは
    app/change_requests/comments.py。`fingerprint`は書いたときに見ていた説明書の sha256。
    リンクを作り直してもコメントは CR に残る(リンクが消えたら`share_link_id`を NULL にする)。
    """

    __tablename__ = "change_request_comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("change_request_comments.id", ondelete="CASCADE"),
        nullable=True,
    )
    share_link_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("change_request_share_links.id", ondelete="SET NULL"),
        nullable=True,
    )
    author_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    author_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
