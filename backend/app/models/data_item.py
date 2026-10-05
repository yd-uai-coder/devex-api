import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, PortableJSON

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.project import Project


class DataItem(Base):
    """プロジェクト共通のデータ辞書1件分を表すORMモデル。

    `fields`は{name, type?, required?}の配列(app/uml/domain/data_item.pyのDataItemField相当)。
    DFDの全フローはこのテーブルの行をidで参照し、自由記述ラベルを持たない。
    project 単位のJSONBではなく専用テーブルにしているのは、項目単位のCRUD・一意性制約・
    参照検証(どこからも参照されないデータ項目が無いか)を素直に書けるため。
    """

    __tablename__ = "data_items"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_data_items_project_id_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # fields: list[{"name": str, "type": str | None, "required": bool | None}]
    fields: Mapped[list[dict]] = mapped_column(PortableJSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="data_items")
