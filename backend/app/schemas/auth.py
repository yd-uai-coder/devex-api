from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    """ログインAPIのリクエストボディ。"""

    email: EmailStr
    password: str


class AccessToken(BaseModel):
    """トークン更新APIのレスポンスボディ。"""

    access_token: str
    token_type: str = "bearer"


class GoogleLoginRequest(BaseModel):
    """Google でのログイン(POST /auth/google)のリクエストボディ。credential は Google Identity
    Services のボタンが返す ID トークン。"""

    credential: str


class GoogleLinkRequest(BaseModel):
    """連携の確かめ(POST /auth/google/link)のリクエストボディ。同じ ID トークンをもう一度送り、
    既存のアカウントのパスワードで本人を確かめる(サーバーに状態を持たない)。"""

    credential: str
    password: str
