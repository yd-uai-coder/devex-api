import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, PortableJSON

if TYPE_CHECKING:
    from app.models.project import Project


class DesignStage(Base):
    """詳細設計モードの段階1つ分(docs/internal_design.md 3.2節⑩)。

    行を持つのは`projects.mode='detailed'`のプロジェクトだけ。行が無い段階は「未着手」。
    `model`が段階の正本(意味モデル)で、詳細設計書はそこから組み立てる。中身の形は段階ごとに
    各Phase(16〜20)で決める。図(DFD・ER・構成図)は`uml_diagrams`・`data_items`を使う。

    `version`は内容の楽観ロック用で、承認しても増やさない(UML図と同じ)。`approved_version`は
    最後に承認したときの`version`、`input_fingerprint`は承認したときの入力の版
    (app/detailed_design/stages.py参照)。

    `generation_*`はAIの下書きの生成の状態。生成中は保存・承認できない(AIの結果で
    人の編集を上書きしないため)。
    """

    __tablename__ = "design_stages"
    __table_args__ = (
        UniqueConstraint("project_id", "stage", name="uq_design_stages_project_stage"),
        CheckConstraint("stage BETWEEN 1 AND 8", name="ck_design_stages_stage_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # status: 'draft'(AIの下書き)/'regenerated'(AIが作り直した・未承認)/
    # 'reviewing'(人が編集した)/'approved'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    model: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_fingerprint: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    # AIの下書きの生成の状態。レビューの状態`status`とは別の軸(UML図と同じ)。
    # None = まだ生成していない / 'generating' / 'completed' / 'failed'
    generation_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 生成を始めた時刻。止まった生成(15分超)の回収に使う(app/services/generation_staleness.py)
    generation_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="design_stages")
