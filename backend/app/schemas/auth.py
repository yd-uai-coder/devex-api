from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    """ログインAPIのリクエストボディ。"""

    email: EmailStr
    password: str


class TokenPair(BaseModel):
    """ログイン成功時に返すアクセストークンとリフレッシュトークンの組。"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """トークン更新・ログアウトAPIのリクエストボディ。"""

    refresh_token: str


class AccessToken(BaseModel):
    """トークン更新APIのレスポンスボディ。"""

    access_token: str
    token_type: str = "bearer"
