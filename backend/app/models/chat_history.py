import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

# 型チェック時の解決用。
# 通常の実行時には False -> importされない（循環import回避のため）
if TYPE_CHECKING:
    from app.models.project import Project


class ChatHistory(Base):
    """プロジェクトのヒアリングチャットにおける1件の発言を表すORMモデル。

    sender: 'user'(利用者) / 'ai'(AI応答) / 'intake'(初期ヒアリング入力・添付ファイル抽出結果の記録)
    / 'others'(生成ドキュメントの自己診断結果の記録)。
    """

    __tablename__ = "chat_histories"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    # 送信者タイプ ('user' / 'ai' / 'intake': 初期ヒアリング入力の記録 / 'others': ドキュメント自己診断結果の記録)
    sender: Mapped[str] = mapped_column(String(20), nullable=False)

    #メッセージ本文
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="chat_histories")
