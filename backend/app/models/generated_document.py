import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.project import Project


class GeneratedDocument(Base):
    """生成された設計書1バージョン分を表すORMモデル。

    doc_type: 'requirements' / 'external_design' / 'internal_design' / 'implementation_plan'。
    バージョニング方針: 再生成時は上書きせず新バージョンを追加する。同一project_id+doc_typeにつき
    直近3バージョンまで保管し、4件目の生成時に最も古いバージョンを削除する(app/repositories/
    generated_document.py の create_version が担う)。
    """

    __tablename__ = "generated_documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    # doc_type：種別 ('requirements', 'external_design', 'internal_design', 'implementation_plan')
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False) # 生成されたMarkdownテキスト
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1) # バージョン番号
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


    project: Mapped["Project"] = relationship(back_populates="generated_documents")
