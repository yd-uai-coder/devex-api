from fastapi import APIRouter, Response, status

from app.api.deps import (
    REFRESH_TOKEN_COOKIE_NAME,
    ClientIpDep,
    RedisDep,
    RefreshTokenCookieDep,
    SessionDep,
)
from app.core.config import settings
from app.schemas.auth import AccessToken, LoginRequest
from app.schemas.user import UserCreate, UserRead
from app.services.auth import AuthService
from app.services.auth_rate_limit import AuthRateLimiter
from app.services.user import UserService

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_TOKEN_MAX_AGE_SECONDS = 14 * 24 * 3600
_REFRESH_TOKEN_COOKIE_PATH = "/api/v1/auth"

def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """リフレッシュトークンをhttpOnly Secure Cookieに設定する。pathを/api/v1/authに限定し、
    認証系以外のAPIへの送信を防ぐ(不要な露出面を減らす)。samesite=laxは同一サイト内の遷移・
    リロードでは送信されつつ、クロスサイトの単純なPOST等では送信されないバランス。secureは
    本番(ENVIRONMENT=production)でのみ強制する(開発環境はHTTPのため常時強制すると送信されなくなる)。"""
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=_REFRESH_TOKEN_MAX_AGE_SECONDS,
        path=_REFRESH_TOKEN_COOKIE_PATH,
    )

@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate, session: SessionDep, redis: RedisDep, client_ip: ClientIpDep
) -> UserRead:
    """新規ユーザーを登録する。メール重複時はAppErrorが伝播しハンドラがHTTP応答に変換する。
    IP 単位でレート制限する(大量登録の対策)。"""
    await AuthRateLimiter(redis).enforce_register(ip=client_ip)
    user = await UserService(session).create_user(
        email=payload.email, password=payload.password, full_name=payload.full_name
    )
    return UserRead.model_validate(user)


@router.post("/login", response_model=AccessToken)
async def login(
    payload: LoginRequest,
    response: Response,
    session: SessionDep,
    redis: RedisDep,
    client_ip: ClientIpDep,
) -> AccessToken:
    """メールアドレスとパスワードで認証し、アクセストークンをレスポンスボディで、
    リフレッシュトークンをhttpOnly Cookieで発行する(レスポンスボディにはrefresh_tokenを含めない)。
    認証の前に IP 単位・メール単位でレート制限する(総当たりの対策)。"""
    await AuthRateLimiter(redis).enforce_login(ip=client_ip, email=payload.email)
    auth_service = AuthService(session, redis)
    user = await auth_service.authenticate(email=payload.email, password=payload.password)
    access_token, refresh_token = await auth_service.issue_tokens(user)
    _set_refresh_cookie(response, refresh_token)
    return AccessToken(access_token=access_token)


@router.post("/refresh", response_model=AccessToken)
async def refresh(
    refresh_token: RefreshTokenCookieDep, session: SessionDep, redis: RedisDep
) -> AccessToken:
    """Cookie中の有効なリフレッシュトークンから新しいアクセストークンを発行する。"""
    auth_service = AuthService(session, redis)
    access_token = await auth_service.refresh_access_token(refresh_token)
    return AccessToken(access_token=access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    refresh_token: RefreshTokenCookieDep,
    session: SessionDep,
    redis: RedisDep,
) -> None:
    """リフレッシュトークンをRedisから削除して以後の再利用を無効化し、Cookieも削除する。"""
    await AuthService(session, redis).revoke_refresh_token(refresh_token)
    response.delete_cookie(REFRESH_TOKEN_COOKIE_NAME, path=_REFRESH_TOKEN_COOKIE_PATH)
