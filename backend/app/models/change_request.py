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
    from app.models.review import ReviewRun


class ChangeRequest(Base):
    """変更要求(CR)1つ。合意済みの版(束)を基準にした仕様の変更の申し出と、その状態・合意の記録を
    持つ(docs/internal_design.md 3.2節⑯)。

    `number`はプロジェクトの中の連番(画面では CR-1, CR-2…)。`status`は
    app/change_requests/states.py の状態。背景は出どころの種類(`background_source`)・日付
    (日の粒度。任意)・メモ。対象は基準の束の要件定義書にある要件 ID と変更の種類の組の一覧
    (`targets`。`[{requirement_id, kind}]`、app/change_requests/targets.py)と、新しい要件を足すかの
    印(`adds_requirement`)。`origins`は新しい要件を足す CR の予測の起点(`[{key, label}]`。基準の
    束の要件・画面・API・段階1 の処理。NULL は起点なし)。`options`は選択肢(2〜3の案とおすすめ。
    app/change_requests/options.py の保存の形。NULL は案なし)。`derived_from_id`は、却下した CR を
    写して作ったときの元の CR。
    `decision_*`は合意・却下の記録(合意済みの版と同じ項目。判断の前は NULL)。`decision_option`は
    合意で選んだ案の番号(1始まり。案の無い CR・却下は NULL)。選んだ案の対象が`targets`になる。
    `decision_source`は判断の出どころ(developer = 開発者が記録 / share_link = クライアントが共有
    リンクで押した。app/change_requests/sharing.py)、`decision_fingerprint`は判断したときの説明書の
    sha256(共有リンクではクライアントが見た版)。どちらもこの記録を足す前の判断は NULL。
    `predicted_impact`は判断の時点の影響の予測(app/change_requests/prediction.py の保存の形。
    判断の前と取り下げは NULL で、求めるたびに基準の束から作る)。`applied_impact`は反映(CR を
    渡した文書の再生成)のときに付いた印の写し(app/detailed_design/impact/marks.py の Mark の形の
    一覧。やり直しはキーで重ねる。反映の前は NULL)。予測との比較に使う(段階の印は承認で消えるため)。
    `document_checks`は文書の側の確認の一覧(保存した予測の画面・API)のうち、確認済みにしたキー
    (NULL は未確認だけ。app/change_requests/verification.py)。`conflict_checks`は重なる CR の競合を
    「今の文と見比べて確かめた」の印(`[{key, seq}]`。seq は確かめた時点の最新の合意の記録。
    app/change_requests/conflicts.py)。`included_baseline_id`は検証済みの後に
    入れた新しい合意済みの版(束)。結んだ設計レビューは ChangeRequestReview に持つ。
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
    targets: Mapped[list[dict]] = mapped_column(PortableJSON, nullable=False, default=list)
    adds_requirement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    origins: Mapped[list[dict] | None] = mapped_column(PortableJSON, nullable=True)
    options: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
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
    decision_option: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    predicted_impact: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    applied_impact: Mapped[list[dict] | None] = mapped_column(PortableJSON, nullable=True)
    document_checks: Mapped[list[str] | None] = mapped_column(PortableJSON, nullable=True)
    conflict_checks: Mapped[list[dict] | None] = mapped_column(PortableJSON, nullable=True)
    included_baseline_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("baselines.id", ondelete="SET NULL"), nullable=True
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
    # 束への外部キーが2つ(基準・入れた束)あるので、どちらを使うかを示す
    baseline: Mapped["Baseline"] = relationship(foreign_keys=[baseline_id])
    included_baseline: Mapped["Baseline | None"] = relationship(
        foreign_keys=[included_baseline_id]
    )
    reviews: Mapped[list["ChangeRequestReview"]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
        order_by="ChangeRequestReview.linked_at",
    )
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
    `refs`は行が指すもの(反映: 作った文書の版 `{"documents": [{doc_type, version}], "model"}`、
    段階の反映: `{"stage", "version", "model"}`、
    レビューを結ぶ: `{"review_run_id", "gate", "model"}`、検証:
    `{"mark_count", "document_count", "review_run_ids"}`、束に入れる: `{"baseline_number"}`)。
    指すものの無い行は NULL。
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
    refs: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)

    change_request: Mapped[ChangeRequest] = relationship(back_populates="events")


class ChangeRequestReview(Base):
    """CR に結んだ設計レビューの回(docs/internal_design.md 3.2節⑲)。

    反映済みの間に、反映の後に流した回から開発者が選んで結ぶ(複数可。外す操作は無い)。
    `linked_by`は結んだ開発者。CR・回のどちらが消えても一緒に消える。
    """

    __tablename__ = "change_request_reviews"

    change_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        primary_key=True,
    )
    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("review_runs.id", ondelete="CASCADE"), primary_key=True
    )
    linked_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    change_request: Mapped[ChangeRequest] = relationship(back_populates="reviews")
    review_run: Mapped["ReviewRun"] = relationship()
