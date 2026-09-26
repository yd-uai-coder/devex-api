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
from app.models.project import Project
from app.models.user import User
from app.repositories.project import ProjectRepository
from app.services.errors import ProjectNotFoundError
from app.services.user import UserService

_bearer_scheme = HTTPBearer(auto_error=False)

type SessionDep = Annotated[AsyncSession, Depends(get_db)]
type RedisDep = Annotated[Redis, Depends(get_redis)]


def get_client_ip(request: Request) -> str:
    """接続元 IP(認証前エンドポイントのレート制限キー)。nginx 配下では uvicorn の
    `--proxy-headers` により X-Forwarded-For の値が入る。"""
    return request.client.host if request.client else "unknown"


type ClientIpDep = Annotated[str, Depends(get_client_ip)]

REFRESH_TOKEN_COOKIE_NAME = "refresh_token"

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


type CurrentUserDep = Annotated[User, Depends(get_current_user)]

def get_refresh_token_from_cookie(request: Request) -> str:
    """Cookieからリフレッシュトークンを取り出す。JSONボディでは受け取らない
    (httpOnly Cookieのみを正規の受け渡し経路とする)。無ければ生のHTTPExceptionで401にする
    (get_current_userの認証境界と同じ設計思想: 失敗理由を漏らさず一律401)。"""
    token = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME)
    if token is None:
        raise _credentials_exception
    return token


type RefreshTokenCookieDep = Annotated[str, Depends(get_refresh_token_from_cookie)]


async def get_current_project(
    project_id: uuid.UUID, session: SessionDep, current_user: CurrentUserDep
) -> Project:
    """パスパラメータ`project_id`と認証済みユーザーから、所有者チェック済みのProjectを取得する
    (`/projects/{project_id}/...`系ルート共通の依存関数)。他ユーザーのプロジェクト・存在しないIDは
    一律404にする(存在有無を漏らさないため、権限エラーではなくNotFoundとして扱う)。"""
    project = await ProjectRepository(session).get_by_id(project_id, user_id=current_user.id)
    if project is None:
        raise ProjectNotFoundError(f"Project {project_id} not found")
    return project


type CurrentProjectDep = Annotated[Project, Depends(get_current_project)]
