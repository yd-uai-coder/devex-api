from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    """ログインAPIのリクエストボディ。"""

    email: EmailStr
    password: str


class AccessToken(BaseModel):
    """トークン更新APIのレスポンスボディ。"""

    access_token: str
    token_type: str = "bearer"
