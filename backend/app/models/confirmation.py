import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.project import Project


class ConfirmationItemRecord(Base):
    """確認事項1つの、利用者が決めた欄(docs/internal_design.md 3.2節㉑)。

    一覧そのものは保存しない(今の要件定義書と観点の表から毎回組み立てる。app/confirmations)。
    ここに持つのは、項目のキーごとの決める機会・状態・メモだけ。利用者が欄を決めたとき(と、
    「採用にする」で確認済みにしたとき)に行を作り、以後は上書きする。

    key: 項目のキー(`req:FR-001`・`user:U-001`・`point:<観点のキー>`)。kind: 項目の
    種類(`app.confirmations.ConfirmationKind`)。label: 保存した時点の表示名(項目が一覧から
    消えた後も出すため)。opportunity: 決める機会(未設定は NULL)。status: 状態(未確認・
    確認済み・却下)。changed_by_change_request_number: CR の反映で文が変わり、確認済みから
    未確認に戻したときの CR の番号(利用者が状態を決め直すと NULL に戻す)。
    """

    __tablename__ = "confirmation_items"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_confirmation_items_project_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    opportunity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unconfirmed")
    memo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    changed_by_change_request_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="confirmation_items")
