import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, PortableJSON

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.project import Project


class UmlGenerationRun(Base):
    """UML図のAI生成リクエスト1回分の履歴(Phase 10)。

    図ごとの`generation_status`/`generation_error`はポーリング用に「最新の状態」だけを持つ。
    一括生成の途中でクォータ超過・トークン上限で止まった場合に、どの対象が生成され、どれが
    どの理由で止まり、どれが未着手のまま残ったかを後から確認できるよう、リクエスト単位の履歴を
    別テーブルに残す。`results`は対象ごとに
    {subject, outcome: succeeded|failed|skipped, reason_code, message, diagram_id}。
    """

    __tablename__ = "uml_generation_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    notation: Mapped[str] = mapped_column(String(50), nullable=False)
    # requested: [{"subject": str, "diagram_id": str}, ...](受け付けた順)
    requested: Mapped[list[dict]] = mapped_column(PortableJSON, nullable=False)
    # status: 'running'/'completed'/'partial'/'failed'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    results: Mapped[list[dict]] = mapped_column(PortableJSON, nullable=False, default=list)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="uml_generation_runs")
