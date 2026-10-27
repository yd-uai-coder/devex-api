import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.project import Project


class Material(Base):
    """プロジェクトの途中から足す資料1件(議事録・メール・資料。docs/internal_design.md 3.2節)。

    合意済みの内容と比べて違いを拾うための材料で、ヒアリングの根拠には使わない(作成時の添付資料
    `intake_files`とは別の表)。本文は貼り付けか、txt / md / pdf から取り出したもの(元のファイルは
    持たない)。本文は切り詰めずに持つ(違いの根拠の引用を本文で確かめるため。長すぎるものは作成時に
    断る)。

    kind: 'minutes'(議事録) / 'email'(メール) / 'document'(資料)。
    source: 'paste'(貼り付け) / 'txt' / 'md' / 'pdf'。`filename`はファイルから足したときの名前。
    material_date: 資料の日付(会議の日・メールの日。足した日時`created_at`とは別)。
    """

    __tablename__ = "materials"
    __table_args__ = (Index("ix_materials_project_created", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    material_date: Mapped[date] = mapped_column(Date, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="materials")
