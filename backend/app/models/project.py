import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, PortableJSON

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.chat_history import ChatHistory
    from app.models.generated_document import GeneratedDocument
    from app.models.intake_file import IntakeFile
    from app.models.user import User


class Project(Base):
    """1つの開発案件(アイデア)単位を表すORMモデル。チャット履歴・生成ドキュメントの親となる。"""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # status: 'interviewing'(ヒアリング中) / 'generating'(生成中) / 'completed'(完了) /
    # 'revising'(修正中。completed後に新規チャットメッセージを送るとここへ遷移する)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="interviewing")
    # intake: 初期ヒアリング入力(system_overview/goals_raw/notes_raw/environment)をそのまま保持する
    intake: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)
    # template_id：テンプレートの永続化
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("prompt_templates.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship()
    # back_populates="table_name"：相手モデル側とのrelationship"project"と対応させる
    # cascade="all, delete-orphan": プロジェクト削除時に配下のチャット履歴・生成物も併せて削除する
    # 対多リレーション　list[相手モデルタイプ]
    chat_histories: Mapped[list["ChatHistory"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ChatHistory.created_at"
    )
    generated_documents: Mapped[list["GeneratedDocument"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    intake_files: Mapped[list["IntakeFile"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
