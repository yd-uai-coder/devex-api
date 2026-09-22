import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import TokenType, decode_token
from app.infrastructure.redis import get_redis
from app.models.user import User
from app.services.user import UserService

_bearer_scheme = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]


def get_client_ip(request: Request) -> str:
    """接続元 IP(認証前エンドポイントのレート制限キー)。nginx 配下では uvicorn の
    `--proxy-headers` により X-Forwarded-For の値が入る。"""
    return request.client.host if request.client else "unknown"


ClientIpDep = Annotated[str, Depends(get_client_ip)]

_credentials_exception = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    """Bearerトークンを検証し、対応するアクティブなユーザーを返す（失敗理由は問わず一律401にする）。"""
    if credentials is None:
        raise _credentials_exception

    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise _credentials_exception from exc

    if payload.get("type") != TokenType.ACCESS.value:
        # リフレッシュトークン等、アクセストークン以外は認証に使わせない
        raise _credentials_exception

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise _credentials_exception from exc

    user = await UserService(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise _credentials_exception
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]
