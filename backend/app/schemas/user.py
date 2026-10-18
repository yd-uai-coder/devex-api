import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    StringConstraints,
    field_validator,
)

from app.core.password_policy import password_violations

# ユーザーネームの変更で受ける値(前後の空白を除いて1〜255文字。列の長さと同じ上限)
type UserNameText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class UserBase(BaseModel):
    """ユーザー関連スキーマ間で共通するフィールドをまとめた基底クラス。"""

    email: EmailStr
    user_name: str | None = None


class UserCreate(UserBase):
    """ユーザー登録APIのリクエストボディ。"""

    password: str


class UserRead(UserBase):
    """ユーザー情報をAPIレスポンスとして返す際のスキーマ（パスワードは含まない）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    is_superuser: bool
    created_at: datetime
    # 設計レビューの設定(保存値)。使うかどうかは review_effective で見る
    review_enabled: bool = False
    # レビューを使えるアカウントか(superuser、またはレビューの用途の AI の自分のキーがある)と、
    # 実効値(使えて、設定がオン)。キーの登録を読むので、ルートが app/api/user_read.py で埋める
    review_allowed: bool = False
    review_effective: bool = False
    # パスワードを持つか・Google と連携しているか(マイページの出し分けに使う。値そのものは返さない)
    has_password: bool = False
    google_linked: bool = False


class ReviewSettingsUpdate(BaseModel):
    """設計レビューの設定の変更のリクエストボディ。"""

    review_enabled: bool


class UserNameUpdate(BaseModel):
    """ユーザーネームの変更(PATCH /users/me)のリクエストボディ。"""

    user_name: UserNameText


class EmailUpdate(BaseModel):
    """メールアドレスの変更(PUT /users/me/email)のリクエストボディ。今のパスワードで本人を
    確かめる。"""

    new_email: EmailStr
    current_password: str


class PasswordUpdate(BaseModel):
    """パスワードの変更(PUT /users/me/password)のリクエストボディ。新しいパスワードには登録の
    画面と同じ規則を課す(合わなければ 422)。"""

    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_policy(cls, value: str) -> str:
        return _require_policy(value)


class PasswordSet(BaseModel):
    """パスワードの設定(POST /users/me/password)のリクエストボディ。パスワードを持たない
    (Google だけの)利用者が使う。規則は変更と同じ(合わなければ 422)。"""

    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_policy(cls, value: str) -> str:
        return _require_policy(value)


class GoogleLinkCurrentRequest(BaseModel):
    """マイページからの Google の連携(POST /users/me/google)のリクエストボディ。credential は
    Google のボタンが返す ID トークン。今のパスワードで本人を確かめる(盗まれたセッションで他人の
    Google を足させない)。"""

    credential: str
    current_password: str


def _require_policy(value: str) -> str:
    """新しいパスワードに登録の画面と同じ規則を課す(変更と設定で共有する)。"""
    violations = password_violations(value)
    if violations:
        raise ValueError(" / ".join(violations))
    return value
