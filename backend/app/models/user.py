import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Uuid, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """アプリケーションの利用者を表すORMモデル。"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    # Google だけで登録した利用者は null(パスワードでのログインはできない)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 連携した Google アカウントの識別子(ID トークンの sub)。連携していなければ null
    google_sub: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 設計レビューの設定(本人が切り替える。初期値はオフ)。使えるのは superuser だけで、実効値は
    # app/review/policy.py の review_effective がサーバーで判定する
    review_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
