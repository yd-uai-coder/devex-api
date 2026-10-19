import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
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
    from app.models.project import Project


class Baseline(Base):
    """合意済みの版(束)1つ。ある時点の文書と段階を束ね、クライアントとの合意の記録を持つ
    (docs/internal_design.md 3.2節⑭)。

    `number`はプロジェクトの中の連番(画面では B-1, B-2…)。合意の基準なので、作った後は直さず
    消さない(プロジェクトの削除の CASCADE でだけ消える)。記録の誤りは新しい束で直す。
    `counterpart`は合意の相手(自由文。空でもよい)、`agreed_at`は合意した日時(記録した日時
    `created_at`とは別)、`recorded_by`は記録した開発者。
    """

    __tablename__ = "baselines"
    # 番号は(プロジェクト)の中で一意。並行して作ったときも DB で止める
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_baselines_project_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    counterpart: Mapped[str | None] = mapped_column(String(200), nullable=True)
    agreed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    memo: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="baselines")
    items: Mapped[list["BaselineItem"]] = relationship(
        back_populates="baseline", cascade="all, delete-orphan", order_by="BaselineItem.position"
    )


class BaselineItem(Base):
    """束の写し1件(文書1つ、または段階1つ。docs/internal_design.md 3.2節⑮)。

    key: 'requirements' / 'external_design' / 'test_spec'(文書)、'stage:1'〜'stage:8'(段階)。
    included: 束に入っているか(文書はその版があるか、段階は承認の写しがあるか)。入っていない
    ものも行を作り、そのときの状態を残す。
    state: 段階は束を作った時点の状態(StageState。承認の後に編集した・古いも残す)、文書は
    'present' / 'absent'。
    source_version: 元の版(文書の版の番号、段階の`approved_version`)。
    content: 文書の md(段階は None)。model: 文書の意味モデル、段階の`approved_model`(段階3は
    承認したときの ER の写しを含む)。generated_model: 文書を生成したモデル名。
    `generated_documents`(直近3版で巡回)・`design_stages`(1行を上書き)から切り離して凍結する。
    """

    __tablename__ = "baseline_items"
    __table_args__ = (UniqueConstraint("baseline_id", "key", name="uq_baseline_items_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    baseline_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("baselines.id", ondelete="CASCADE"), nullable=False
    )
    # 文書(上流から)→ 段階1〜8 の並び(0始まり)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    key: Mapped[str] = mapped_column(String(30), nullable=False)
    included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    source_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    generated_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    baseline: Mapped[Baseline] = relationship(back_populates="items")
