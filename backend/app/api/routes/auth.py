from fastapi import APIRouter, status

from app.api.deps import ClientIpDep, RedisDep, SessionDep
from app.schemas.auth import AccessToken, LoginRequest, RefreshRequest, TokenPair
from app.schemas.user import UserCreate, UserRead
from app.services.auth import AuthService
from app.services.auth_rate_limit import AuthRateLimiter
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
        email=payload.email, password=payload.password, full_name=payload.full_name
    )
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest, session: SessionDep, redis: RedisDep, client_ip: ClientIpDep
) -> TokenPair:
    """メールアドレスとパスワードで認証し、アクセストークンとリフレッシュトークンを発行する。
    認証の前に IP 単位・メール単位でレート制限する(総当たりの対策)。"""
    await AuthRateLimiter(redis).enforce_login(ip=client_ip, email=payload.email)
    auth_service = AuthService(session, redis)
    user = await auth_service.authenticate(email=payload.email, password=payload.password)
    access_token, refresh_token = await auth_service.issue_tokens(user)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=AccessToken)
async def refresh(payload: RefreshRequest, session: SessionDep, redis: RedisDep) -> AccessToken:
    """有効なリフレッシュトークンから新しいアクセストークンを発行する。"""
    auth_service = AuthService(session, redis)
    access_token = await auth_service.refresh_access_token(payload.refresh_token)
    return AccessToken(access_token=access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, session: SessionDep, redis: RedisDep) -> None:
    """リフレッシュトークンをRedisから削除し、以後の再利用を無効化する。"""
    await AuthService(session, redis).revoke_refresh_token(payload.refresh_token)
