import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, computed_field

from app.review import review_effective


class UserBase(BaseModel):
    """ユーザー関連スキーマ間で共通するフィールドをまとめた基底クラス。"""

    email: EmailStr
    full_name: str | None = None


class UserCreate(UserBase):
    """ユーザー登録APIのリクエストボディ。"""

    password: str


class UserUpdate(BaseModel):
    """ユーザー更新APIのリクエストボディ（指定したフィールドのみ更新する想定）。"""

    full_name: str | None = None
    password: str | None = None


class UserRead(UserBase):
    """ユーザー情報をAPIレスポンスとして返す際のスキーマ（パスワードは含まない）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    is_superuser: bool
    created_at: datetime
    # 設計レビューの設定(保存値)。使うかどうかは review_effective で見る
    review_enabled: bool = False

    @computed_field
    @property
    def review_effective(self) -> bool:
        """設計レビューの実効値(superuser かつ設定がオン)。画面はこれで出し分ける。"""
        return review_effective(
            is_superuser=self.is_superuser, review_enabled=self.review_enabled
        )


class ReviewSettingsUpdate(BaseModel):
    """設計レビューの設定の変更のリクエストボディ。"""

    review_enabled: bool
