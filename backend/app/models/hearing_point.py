import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, PortableJSON

if TYPE_CHECKING:
    from app.models.project import Project


class HearingPoint(Base):
    """ヒアリングの観点1つの状態(docs/internal_design.md 3.2節⑪)。

    観点の定義(名前・問いの例・規模ごとの必須)はコードに持つ(app/hearing/points.py)。ここに
    持つのはプロジェクトごとの状態だけ。行はヒアリングの判定のたびに、全観点の分をまとめて書き直す。
    """

    __tablename__ = "hearing_points"
    __table_args__ = (
        UniqueConstraint("project_id", "point_key", name="uq_hearing_points_project_point"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    point_key: Mapped[str] = mapped_column(String(40), nullable=False)
    # status: 'decided'(ユーザーが決定)/'adopted'(AIの提案を採用)/'open'(未決)/
    # 'not_applicable'(対象外)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    # 決めたこと
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 根拠の発言(chat_histories.idの文字列のリスト)
    evidence: Mapped[list | None] = mapped_column(PortableJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="hearing_points")
