import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
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
    from app.models.change_request import ChangeRequest
    from app.models.project import Project


class AgreementRecord(Base):
    """要素1つの合意の記録1行(docs/internal_design.md 3.2節⑳)。追記だけで、直さず消さない
    (プロジェクトの削除の CASCADE でだけ消える)。今の合意は、キーごとの最新の行。

    key: 変更影響分析の要素のキー(`req:FR-001`・`screen:SCR-001`・`api:GET /x`)と、
    `users`(利用者と使う場面 1.2 の全体)・`integration:<サービス名>`(外部連携)。
    kind: 要素の種類(`app.agreements.AgreedKind`)。label: 記録した時点の表示名。
    content: 合意した中身(行の model の写し)。NULL は「外れた」(束に入らなかった)。
    origin: 出どころ(`baseline` / `adopt` / `change_request`)。束・CR は`baseline_id`・
    `change_request_id`で指す。seq: プロジェクトの中の記録の順(同じ日時でも順が決まるように)。
    """

    __tablename__ = "agreement_records"
    __table_args__ = (
        UniqueConstraint("project_id", "seq", name="uq_agreement_records_project_seq"),
        Index("ix_agreement_records_project_key", "project_id", "key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    baseline_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("baselines.id", ondelete="CASCADE"), nullable=True
    )
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="agreement_records")
    # 出どころの番号(B-n・CR-n)を出すために読む(リポジトリが selectinload で付ける)
    baseline: Mapped["Baseline | None"] = relationship()
    change_request: Mapped["ChangeRequest | None"] = relationship()
