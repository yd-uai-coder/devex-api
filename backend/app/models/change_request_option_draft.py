import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, PortableJSON


class ChangeRequestOptionDraft(Base):
    """CR の選択肢(案)の LLM の下書き(CR ごとに1行。作り直すと上書き。docs/internal_design.md
    3.2節⑳)。

    `options`は LLM が作った案(app/change_requests/options.py の保存の形)。`status`は
    app/change_requests/option_drafting.py の未確認 / 取り込み済み。取り込むと案が CR の`options`に
    写り、取り込んだ人と日時が`adopted_*`に残る(下書きの案は直さない。直すのは取り込んだ後の案)。
    `fingerprint`は下書きを作った時点の説明書の sha256(照合と、作った後に説明書が変わったかを
    見るため)。`generated_model`は下書きを作ったモデル名。
    """

    __tablename__ = "change_request_option_drafts"
    __table_args__ = (
        UniqueConstraint("change_request_id", name="uq_change_request_option_drafts_cr"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    options: Mapped[dict] = mapped_column(PortableJSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    adopted_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
