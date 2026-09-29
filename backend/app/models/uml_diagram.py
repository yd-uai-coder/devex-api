import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, PortableJSON

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.project import Project


class UmlDiagram(Base):
    """UML設計図1件(component/er/dfdのいずれか)を表すORMモデル。
    docs/internal_design.md 3.2節⑦のテーブル定義に対応する。

    `semantic_model`が正本(Single Source of Truth)。ORM層は生dict(PortableJSON)として
    保持し、型付きへの変換(app.uml.domain.SemanticModelAdapter)はサービス層が担う
    (既存のproject.intake等と同じ「ORMは生dict、型検証はPydantic境界で行う」方針を踏襲)。

    `version`は楽観ロック用(app/repositories/generated_document.pyのversionとは意味が異なる
    新規パターン。既存コードベースに前例が無いためPhase 8で新設する。PUT時にリクエストの
    versionとDB上のversionが一致しない場合はDiagramVersionConflictError(409)にする)。

    親子/階層(parent_diagram_id・level)は持たない。DFD検証規則「上位図と下位図の境界
    フローが一致する」はPhase 10へ申し送り(textbook/Phase-8/Phase-8-introduction.md参照)。
    """

    __tablename__ = "uml_diagrams"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # view: 設計ビュー('structure'/'data'/'dataflow')。notationから一意に決まる
    # (app.uml.domain.NOTATION_TO_VIEW)。
    view: Mapped[str] = mapped_column(String(50), nullable=False)
    # notation: 図記法('component'/'er'/'dfd')
    notation: Mapped[str] = mapped_column(String(50), nullable=False)
    semantic_model: Mapped[dict] = mapped_column(PortableJSON, nullable=False)
    layout_model: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    style_model: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    # status: 'draft'/'reviewing'/'approved'/'exported'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # source_doc_versions: 生成元とした各generated_documentsのバージョン番号
    # (陳腐化検知用、Phase 10〜)
    source_doc_versions: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="uml_diagrams")
