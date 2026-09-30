import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
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

    親子/階層(parent_diagram_id・level)は持たない。DFDは「APIエンドポイント/バッチごとに1枚」の
    フラットな構成に確定した(Phase 10)ため、「上位図と下位図の境界フローが一致する」規則は撤回した。

    `subject`は同じ記法の中で図を識別するキー(component: ''、ER: 全体なら''・部分図なら
    グループ名、DFD: 処理名)。`(project_id, notation, subject)`で一意にし、再生成は同じ行を
    上書きする。`scope`はAIに渡す対象の選択(ER部分図のテーブル名一覧)で、再生成で再利用する。
    `generation_status`はAI生成の状態(generating/completed/failed)で、レビューの状態`status`
    (draft→approved)とは別の軸。
    """

    __tablename__ = "uml_diagrams"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "notation", "subject", name="uq_uml_diagrams_project_notation_subject"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # view: 設計ビュー('structure'/'data'/'dataflow')。notationから一意に決まる
    # (app.uml.domain.NOTATION_TO_VIEW)。
    view: Mapped[str] = mapped_column(String(50), nullable=False)
    # notation: 図記法('component'/'er'/'dfd')
    notation: Mapped[str] = mapped_column(String(50), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    # scope: {"tables": [...]}(ER部分図)。それ以外はNone
    scope: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    semantic_model: Mapped[dict] = mapped_column(PortableJSON, nullable=False)
    layout_model: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    style_model: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    # status: 'draft'/'reviewing'/'approved'/'exported'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # generation_status: 'generating'/'completed'/'failed'
    generation_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="completed", server_default="completed"
    )
    # generation_error: 直近の生成が失敗した理由(ユーザー向けの文言)
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # source_doc_versions: 生成元とした各generated_documentsのバージョン番号
    # (陳腐化検知用。AI生成時に{"internal_design": <version>}を記録する)
    source_doc_versions: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="uml_diagrams")
