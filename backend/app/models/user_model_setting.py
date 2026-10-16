import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserModelSetting(Base):
    """用途(チャット・生成・レビュー・判定)ごとに選んだ provider とモデル。

    `user_id` が null の行はシステムの既定値(scripts/seed.py が `.env` のモデルで書く)。
    ユーザーが選ばない用途は行を持たず、既定値に従う。用途・provider の値は
    app/ai/llm/catalog.py の Purpose・Provider。
    """

    __tablename__ = "user_model_settings"
    # 既定値の行(user_id が null)も用途ごとに1行に絞るため、null を同じ値とみなす(Postgres 15+)
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "purpose",
            name="uq_user_model_settings_user_purpose",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
