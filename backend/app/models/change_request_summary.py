import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ChangeRequestSummary(Base):
    """変更の説明書の任意の要約(CR ごとに1行。作り直すと上書き。docs/internal_design.md 3.2節⑱)。

    `generated_text`は LLM が作った元の文、`text`は人が直した今の文(作った直後は同じ)。
    `status`は app/change_requests/summary.py の未確認 / 確認済み。`fingerprint`は要約を作った・
    確かめた時点の説明書の5節の sha256 で、今の5節と違えば「古い」(状態は保存せず読むときに決める)。
    `generated_model`は要約を作ったモデル名。確かめた人と日時は`confirmed_*`。
    """

    __tablename__ = "change_request_summaries"
    __table_args__ = (
        UniqueConstraint("change_request_id", name="uq_change_request_summaries_cr"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    generated_text: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
