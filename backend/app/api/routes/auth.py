from fastapi import APIRouter, Response, status

from app.api.deps import (
    ClientIpDep,
    GoogleTokenVerifierDep,
    RedisDep,
    RefreshTokenCookieDep,
    SessionDep,
)
from app.api.refresh_cookie import delete_refresh_cookie, set_refresh_cookie
from app.api.user_read import user_read
from app.models.user import User
from app.schemas.auth import AccessToken, GoogleLinkRequest, GoogleLoginRequest, LoginRequest
from app.schemas.user import UserCreate, UserRead
from app.services.auth import AuthService
from app.services.auth_rate_limit import AuthRateLimiter
from app.services.google_auth import GoogleAuthService
from app.services.user import UserService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserCreate, session: SessionDep, redis: RedisDep, client_ip: ClientIpDep
) -> UserRead:
    """新規ユーザーを登録する。メール重複時はAppErrorが伝播しハンドラがHTTP応答に変換する。
    IP 単位でレート制限する(大量登録の対策)。"""
    await AuthRateLimiter(redis).enforce_register(ip=client_ip)
    user = await UserService(session).create_user(
        email=payload.email, password=payload.password, user_name=payload.user_name
    )
    return await user_read(session, user)


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
    set_refresh_cookie(response, refresh_token)
    return AccessToken(access_token=access_token)


@router.post("/google", response_model=AccessToken)
async def google_login(
    payload: GoogleLoginRequest,
    response: Response,
    session: SessionDep,
    redis: RedisDep,
    client_ip: ClientIpDep,
    verifier: GoogleTokenVerifierDep,
) -> AccessToken:
    """Google の ID トークンでログインする(だれのメールでもなければ新しく登録する)。返し方は
    /auth/login と同じ。同じメールのアカウントが連携していなければ 409 GOOGLE_LINK_REQUIRED。"""
    await AuthRateLimiter(redis).enforce_google(ip=client_ip)
    identity = await verifier.verify(payload.credential)
    user = await GoogleAuthService(session).login_or_register(identity)
    return await _issue_login(response, session, redis, user)


@router.post("/google/link", response_model=AccessToken)
async def google_link(
    payload: GoogleLinkRequest,
    response: Response,
    session: SessionDep,
    redis: RedisDep,
    client_ip: ClientIpDep,
    verifier: GoogleTokenVerifierDep,
) -> AccessToken:
    """同じメールの既存のアカウントのパスワードで本人を確かめ、Google と連携してログインする。
    パスワードの試行は、ログインと同じ上限(IP・メール)でも数える。"""
    limiter = AuthRateLimiter(redis)
    await limiter.enforce_google(ip=client_ip)
    identity = await verifier.verify(payload.credential)
    await limiter.enforce_login(ip=client_ip, email=identity.email)
    user = await GoogleAuthService(session).link(identity, password=payload.password)
    return await _issue_login(response, session, redis, user)


async def _issue_login(
    response: Response, session: SessionDep, redis: RedisDep, user: User
) -> AccessToken:
    """access を本文で、refresh を Cookie で出す(Google のログインと連携で共有する)。"""
    access_token, refresh_token = await AuthService(session, redis).issue_tokens(user)
    set_refresh_cookie(response, refresh_token)
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
    delete_refresh_cookie(response)
