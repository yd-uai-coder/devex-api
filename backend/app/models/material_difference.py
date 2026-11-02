import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, PortableJSON

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.material import Material


class MaterialExtraction(Base):
    """資料1件の違いの取り出しの直近の回(資料ごとに1行。やり直すと上書き。docs/internal_design.md
    3.2節)。

    `baseline_number`は比べた束(取り出しの時点の最新の束)、`freshness`はその時点の束の鮮度
    (app/change_requests/freshness.py の結果を応答の形で写したもの。束が古ければ知らせたことを
    残す)。`generated_model`は取り出したモデル名。
    """

    __tablename__ = "material_extractions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("materials.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    baseline_number: Mapped[int] = mapped_column(Integer, nullable=False)
    freshness: Mapped[dict] = mapped_column(PortableJSON, nullable=False)
    generated_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    material: Mapped["Material"] = relationship(back_populates="extraction")


class MaterialDifference(Base):
    """資料から取り出した違い1件(docs/internal_design.md 3.2節)。

    kind: app/materials/differences.py の 'add' / 'change' / 'delete' / 'question'。
    target_key: 対象の合意の要素のキー(`req:FR-001`。追加は空)。origin_key: 追加の起点。
    status: 'unconfirmed'(未確認) / 'rejected'(検査で弾いた。`reasons`に理由) / 'converted'
    (CR にした。`change_request_id`にその CR) / 'dismissed'(捨てた。`dismiss_reason`に理由)。
    やり直すと、未確認と弾いたは置き換わり、CR にした・捨てたは残る。`baseline_number`は
    取り出したときに比べた束、`position`は資料の中の並び(取り出した順。やり直しで足したものは
    後ろに続く)。
    """

    __tablename__ = "material_differences"
    __table_args__ = (Index("ix_material_differences_material", "material_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    target_key: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    origin_key: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reasons: Mapped[list] = mapped_column(PortableJSON, nullable=False, default=list)
    dismiss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    material: Mapped["Material"] = relationship(back_populates="differences")
