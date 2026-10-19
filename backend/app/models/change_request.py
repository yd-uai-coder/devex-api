import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
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

from app.core.database import Base, PortableJSON

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.baseline import Baseline
    from app.models.project import Project


class ChangeRequest(Base):
    """変更要求(CR)1つ。合意済みの版(束)を基準にした仕様の変更の申し出と、その状態・合意の記録を
    持つ(docs/internal_design.md 3.2節⑯)。

    `number`はプロジェクトの中の連番(画面では CR-1, CR-2…)。`status`は
    app/change_requests/states.py の状態。背景は出どころの種類(`background_source`)・日付
    (日の粒度。任意)・メモ。対象は基準の束の要件定義書にある要件 ID の一覧と、新しい要件を足すかの
    印(`adds_requirement`)。`derived_from_id`は、却下した CR を写して作ったときの元の CR。
    `decision_*`は合意・却下の記録(合意済みの版と同じ項目。判断の前は NULL)。
    状態の遷移は時系列(ChangeRequestEvent)に1行ずつ残す。
    """

    __tablename__ = "change_requests"
    # 番号は(プロジェクト)の中で一意。並行して作ったときも DB で止める
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_change_requests_project_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    background_source: Mapped[str] = mapped_column(String(20), nullable=False)
    background_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    background_memo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_requirement_ids: Mapped[list[str]] = mapped_column(
        PortableJSON, nullable=False, default=list
    )
    adds_requirement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    baseline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("baselines.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    derived_from_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    decision_counterpart: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="change_requests")
    baseline: Mapped["Baseline"] = relationship()
    events: Mapped[list["ChangeRequestEvent"]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
        order_by="ChangeRequestEvent.position",
    )


class ChangeRequestEvent(Base):
    """CR の時系列の1行(作成・状態の遷移・写し。docs/internal_design.md 3.2節⑰)。

    `from_status`は前の状態(作成は None)、`to_status`は後の状態。`action`は操作
    (create / copy / edit / explain / agree / reject / withdraw …。states.py の操作)。
    `note`は取り下げの理由や「CR-2 から写した」などの補足。`actor_id`は操作した開発者。
    """

    __tablename__ = "change_request_events"
    __table_args__ = (
        UniqueConstraint("change_request_id", "position", name="uq_change_request_events_pos"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("change_requests.id", ondelete="CASCADE"), nullable=False
    )
    # 起きた順(0始まり)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    change_request: Mapped[ChangeRequest] = relationship(back_populates="events")
