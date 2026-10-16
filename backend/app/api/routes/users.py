from fastapi import APIRouter, Response

from app.api.deps import ClientIpDep, CurrentUserDep, RedisDep, SessionDep
from app.api.refresh_cookie import set_refresh_cookie
from app.schemas.auth import AccessToken
from app.schemas.user import (
    EmailUpdate,
    PasswordUpdate,
    ReviewSettingsUpdate,
    UserNameUpdate,
    UserRead,
)
from app.services.auth import AuthService
from app.services.auth_rate_limit import AuthRateLimiter
from app.services.user import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUserDep) -> UserRead:
    """アクセストークンから解決した、現在ログイン中のユーザー情報を返す。"""
    return UserRead.model_validate(current_user)


@router.patch("/me", response_model=UserRead)
async def update_current_user(
    body: UserNameUpdate, session: SessionDep, current_user: CurrentUserDep
) -> UserRead:
    """ユーザーネームを変える。"""
    user = await UserService(session).update_user_name(current_user, user_name=body.user_name)
    return UserRead.model_validate(user)


@router.put("/me/email", response_model=UserRead)
async def change_email(
    body: EmailUpdate,
    session: SessionDep,
    redis: RedisDep,
    client_ip: ClientIpDep,
    current_user: CurrentUserDep,
) -> UserRead:
    """今のパスワードで確かめて、メールアドレスを即時に変える(不一致は400、使われているメールは409)。
    今のパスワードの試行はログインと同じ上限で数える(盗まれたセッションでの総当たりの対策)。"""
    await AuthRateLimiter(redis).enforce_login(ip=client_ip, email=current_user.email)
    user = await UserService(session).change_email(
        current_user, new_email=body.new_email, current_password=body.current_password
    )
    return UserRead.model_validate(user)


@router.put("/me/password", response_model=AccessToken)
async def change_password(
    body: PasswordUpdate,
    response: Response,
    session: SessionDep,
    redis: RedisDep,
    client_ip: ClientIpDep,
    current_user: CurrentUserDep,
) -> AccessToken:
    """今のパスワードで確かめてパスワードを変え、その人のリフレッシュトークンを全部失効させる
    (ほかの端末のログインを切る)。今の端末はログインを保つため、新しいアクセストークンを返し、
    リフレッシュトークンを Cookie で出し直す。"""
    await AuthRateLimiter(redis).enforce_login(ip=client_ip, email=current_user.email)
    user = await UserService(session).change_password(
        current_user, current_password=body.current_password, new_password=body.new_password
    )
    auth_service = AuthService(session, redis)
    await auth_service.revoke_all_refresh_tokens(user.id)
    access_token, refresh_token = await auth_service.issue_tokens(user)
    set_refresh_cookie(response, refresh_token)
    return AccessToken(access_token=access_token)


@router.put("/me/review-settings", response_model=UserRead)
async def update_review_settings(
    body: ReviewSettingsUpdate, session: SessionDep, current_user: CurrentUserDep
) -> UserRead:
    """設計レビューのオン・オフを本人が切り替える(superuser 以外は403 REVIEW_NOT_ALLOWED)。"""
    user = await UserService(session).update_review_setting(
        current_user, enabled=body.review_enabled
    )
    return UserRead.model_validate(user)
