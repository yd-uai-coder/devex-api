from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.ai.llm.catalog import Provider
from app.ai.llm.key_check import KeyVerifier, get_key_verifier
from app.api.deps import ClientIpDep, CurrentUserDep, RedisDep, SessionDep
from app.api.refresh_cookie import set_refresh_cookie
from app.api.user_read import user_read
from app.core.config import settings
from app.core.crypto import encryption_available
from app.schemas.auth import AccessToken
from app.schemas.llm_settings import (
    ApiKeyRead,
    ApiKeysRead,
    ApiKeyUpdate,
    ModelSettingsRead,
    ModelSettingsUpdate,
    PurposeSettingRead,
)
from app.schemas.user import (
    EmailUpdate,
    PasswordUpdate,
    ReviewSettingsUpdate,
    UserNameUpdate,
    UserRead,
)
from app.services.auth import AuthService
from app.services.auth_rate_limit import AuthRateLimiter
from app.services.llm_settings_service import LlmSettingsService, PurposeSetting
from app.services.rate_limit import RateLimit, RateLimiter
from app.services.user import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def read_current_user(session: SessionDep, current_user: CurrentUserDep) -> UserRead:
    """アクセストークンから解決した、現在ログイン中のユーザー情報を返す。"""
    return await user_read(session, current_user)


@router.patch("/me", response_model=UserRead)
async def update_current_user(
    body: UserNameUpdate, session: SessionDep, current_user: CurrentUserDep
) -> UserRead:
    """ユーザーネームを変える。"""
    user = await UserService(session).update_user_name(current_user, user_name=body.user_name)
    return await user_read(session, user)


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
    return await user_read(session, user)


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
    """設計レビューのオン・オフを本人が切り替える(使えないアカウントは403 REVIEW_NOT_ALLOWED)。"""
    user = await UserService(session).update_review_setting(
        current_user, enabled=body.review_enabled
    )
    return await user_read(session, user)


def _model_settings_read(rows: list[PurposeSetting]) -> ModelSettingsRead:
    return ModelSettingsRead(settings=[PurposeSettingRead(**asdict(row)) for row in rows])


@router.get("/me/models", response_model=ModelSettingsRead)
async def read_model_settings(
    session: SessionDep, current_user: CurrentUserDep
) -> ModelSettingsRead:
    """用途(チャット・生成・レビュー・判定)ごとの今のモデルと、選べる provider を返す。"""
    rows = await LlmSettingsService(session).get_model_settings(current_user)
    return _model_settings_read(rows)


@router.put("/me/models", response_model=ModelSettingsRead)
async def update_model_settings(
    body: ModelSettingsUpdate, session: SessionDep, current_user: CurrentUserDep
) -> ModelSettingsRead:
    """用途ごとの選択の全体を置き換える(含めない用途は既定値に従う)。カタログに無いモデルは
    422 MODEL_NOT_IN_CATALOG、キーの無い provider は 422 MODEL_PROVIDER_KEY_REQUIRED。"""
    rows = await LlmSettingsService(session).update_model_settings(
        current_user, [(c.purpose, c.provider, c.model) for c in body.choices]
    )
    return _model_settings_read(rows)


@router.get("/me/api-keys", response_model=ApiKeysRead)
async def read_api_keys(session: SessionDep, current_user: CurrentUserDep) -> ApiKeysRead:
    """登録済みのキー(末尾の4文字だけ)と、サーバーに暗号の鍵があるかを返す。"""
    keys = await LlmSettingsService(session).list_api_keys(current_user)
    return ApiKeysRead(
        encryption_available=encryption_available(),
        keys=[ApiKeyRead(**asdict(key)) for key in keys],
    )


@router.put("/me/api-keys/{provider}", response_model=ApiKeyRead)
async def register_api_key(
    provider: Provider,
    body: ApiKeyUpdate,
    session: SessionDep,
    redis: RedisDep,
    current_user: CurrentUserDep,
    verifier: Annotated[KeyVerifier, Depends(get_key_verifier)],
) -> ApiKeyRead:
    """キーを確かめて(モデルの一覧の取得)から暗号化して保存する。無効は 422 API_KEY_INVALID、
    確かめられないときは 502 API_KEY_CHECK_FAILED、暗号の鍵が無ければ 503 ENCRYPTION_UNAVAILABLE。
    試行はユーザーごとに1時間の上限で数える(キーの当てずっぽうの確かめに使わせない)。"""
    limiter = RateLimiter(
        redis,
        resource="api_key_check",
        limits=[RateLimit(window_seconds=3600, max_requests=settings.API_KEY_RATE_LIMIT_PER_HOUR)],
    )
    await limiter.enforce(str(current_user.id))
    key = await LlmSettingsService(session, verifier=verifier).register_api_key(
        current_user, provider, body.api_key
    )
    return ApiKeyRead(**asdict(key))


@router.delete("/me/api-keys/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    provider: Provider, session: SessionDep, current_user: CurrentUserDep
) -> None:
    """キーを消す(無くても 204)。そのキーの provider を選んだ用途は、使うときに既定値に従う。"""
    await LlmSettingsService(session).delete_api_key(current_user, provider)
