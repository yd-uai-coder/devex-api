import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, PortableJSON

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.project import Project


class ReviewRun(Base):
    """設計レビュー1回分(どの区切りの、どの版の文書を、どのモデルでレビューしたか)。

    gate: レビューの区切り。'documents' = 文書の生成の後(簡易は4文書、詳細は要件・外部)。
    段階6・7の承認の後の区切りは後で足す。
    targets: レビューした文書の版 {doc_type: version}。文書の行(generated_documents)には
    外部キーで結ばない ── 文書は直近3版しか残さないため、古い版と一緒にレビューが消えないように
    する。表示中の版と比べて、レビューが今の文書のものかを判定する。
    status: 'running' / 'succeeded' / 'failed'。失敗した回も残す(error_code に理由)。
    """

    __tablename__ = "review_runs"
    # 区切りごとの最新の回を引く
    __table_args__ = (Index("ix_review_runs_project_gate", "project_id", "gate"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    gate: Mapped[str] = mapped_column(String(20), nullable=False)
    targets: Mapped[dict] = mapped_column(PortableJSON, nullable=False, default=dict)
    # trigger: 'auto'(生成の後に自動)/ 'manual'(利用者が「もう一度レビュー」を押した)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # トークン数(費用の確認・プロンプトキャッシュの判断に使う)。失敗した回は NULL
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="review_runs")
    findings: Mapped[list["ReviewFinding"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ReviewFinding.position"
    )


class ReviewFinding(Base):
    """設計レビューの指摘1件(app/review/schemas.py の ReviewFindingOut を保存したもの)。

    decision: 人の採否。'pending'(未判断)/ 'adopted' / 'rejected' / 'held'。
    """

    __tablename__ = "review_findings"
    __table_args__ = (Index("ix_review_findings_run_id", "run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    # 重要度の順に並べた位置(0始まり)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    cause: Mapped[str] = mapped_column(String(20), nullable=False)
    targets: Mapped[list[str]] = mapped_column(PortableJSON, nullable=False, default=list)
    point_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )

    run: Mapped[ReviewRun] = relationship(back_populates="findings")
