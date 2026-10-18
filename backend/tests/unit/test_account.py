"""マイページのアカウント(ユーザーネーム・メール・パスワードの変更)のテスト。

SUT: UserService.update_user_name / change_email / change_password、
     AuthService.revoke_all_refresh_tokens、ルートの関数(users.update_current_user・
     change_email・change_password)と UserNameUpdate の検証
ドライバ: pytest のテスト関数(ルートの関数を直接呼ぶ。既存のルートのテストと同じ形)
スタブ: FakeRedis(リフレッシュトークンの鍵と、今のパスワードの試行の回数)。
     DB はテスト用の SQLite のセッション
"""

import pytest
from fastapi import Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_redis import FakeRedis

from app.api.routes.users import change_email, change_password, update_current_user
from app.core.config import settings
from app.core.security import decode_token, verify_password
from app.models.user import User
from app.schemas.user import EmailUpdate, PasswordUpdate, UserNameUpdate
from app.services.auth import AuthService
from app.services.errors import (
    CurrentPasswordMismatchError,
    RateLimitExceededError,
    UserAlreadyExistsError,
)
from app.services.user import UserService

_PASSWORD = "Old-pass-01"
_NEW_PASSWORD = "New-pass-02"
_IP = "203.0.113.7"


async def _user(session: AsyncSession, email: str = "gil@example.com") -> User:
    return await UserService(session).create_user(
        email=email, password=_PASSWORD, user_name="Gil"
    )


# --- ユーザーネーム ---


async def test_update_user_name_route_saves_trimmed_name(db_session: AsyncSession) -> None:
    user = await _user(db_session)

    read = await update_current_user(UserNameUpdate(user_name="  ギル  "), db_session, user)

    assert read.user_name == "ギル"
    assert user.user_name == "ギル"


@pytest.mark.parametrize("name", ["", "   ", "x" * 256])
def test_user_name_update_rejects_empty_or_too_long(name: str) -> None:
    with pytest.raises(ValidationError):
        UserNameUpdate(user_name=name)


# --- メールアドレス ---


async def test_change_email_with_current_password(db_session: AsyncSession) -> None:
    user = await _user(db_session)

    read = await change_email(
        EmailUpdate(new_email="gil2@example.com", current_password=_PASSWORD),
        db_session,
        FakeRedis(),  # type: ignore[arg-type]
        _IP,
        user,
    )

    assert read.email == "gil2@example.com"


async def test_change_email_rejects_wrong_current_password(db_session: AsyncSession) -> None:
    user = await _user(db_session)

    with pytest.raises(CurrentPasswordMismatchError) as raised:
        await UserService(db_session).change_email(
            user, new_email="gil2@example.com", current_password="wrong"
        )

    assert raised.value.status_code == 400
    assert raised.value.code == "CURRENT_PASSWORD_MISMATCH"
    assert user.email == "gil@example.com"


async def test_change_email_rejects_email_in_use(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    await _user(db_session, email="taken@example.com")

    with pytest.raises(UserAlreadyExistsError):
        await UserService(db_session).change_email(
            user, new_email="taken@example.com", current_password=_PASSWORD
        )


async def test_change_email_to_same_address_is_noop(db_session: AsyncSession) -> None:
    user = await _user(db_session)

    changed = await UserService(db_session).change_email(
        user, new_email="gil@example.com", current_password=_PASSWORD
    )

    assert changed.email == "gil@example.com"


async def test_current_password_attempts_share_login_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_PER_EMAIL_PER_HOUR", 1)
    user = await _user(db_session)
    redis = FakeRedis()
    body = EmailUpdate(new_email="gil2@example.com", current_password="wrong")

    with pytest.raises(CurrentPasswordMismatchError):
        await change_email(body, db_session, redis, _IP, user)  # type: ignore[arg-type]
    with pytest.raises(RateLimitExceededError):
        await change_email(body, db_session, redis, _IP, user)  # type: ignore[arg-type]


# --- パスワード ---


async def test_revoke_all_refresh_tokens_only_for_that_user(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    other = await _user(db_session, email="hal@example.com")
    redis = FakeRedis()
    auth = AuthService(db_session, redis)  # type: ignore[arg-type]
    _, first = await auth.issue_tokens(user)
    _, second = await auth.issue_tokens(user)
    _, others = await auth.issue_tokens(other)

    removed = await auth.revoke_all_refresh_tokens(user.id)

    assert removed == 2
    for token in (first, second):
        claims = decode_token(token)
        assert await redis.exists(f"refresh_token:{claims['sub']}:{claims['jti']}") == 0
    assert await auth.refresh_access_token(others)


async def test_change_password_route_revokes_others_and_keeps_this_device(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    redis = FakeRedis()
    auth = AuthService(db_session, redis)  # type: ignore[arg-type]
    _, other_device = await auth.issue_tokens(user)
    response = Response()

    token = await change_password(
        PasswordUpdate(current_password=_PASSWORD, new_password=_NEW_PASSWORD),
        response,
        db_session,
        redis,  # type: ignore[arg-type]
        _IP,
        user,
    )

    assert verify_password(_NEW_PASSWORD, user.hashed_password)
    assert decode_token(token.access_token)["sub"] == str(user.id)
    claims = decode_token(other_device)
    assert await redis.exists(f"refresh_token:{claims['sub']}:{claims['jti']}") == 0
    # 今の端末には新しい refresh を Cookie で出し直す(Redis にも記録されている)
    cookie = response.headers["set-cookie"]
    assert "refresh_token=" in cookie and "Path=/api/v1/auth" in cookie
    new_refresh = cookie.split("refresh_token=", 1)[1].split(";", 1)[0]
    assert await auth.refresh_access_token(new_refresh)


async def test_change_password_rejects_wrong_current_password(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    redis = FakeRedis()
    auth = AuthService(db_session, redis)  # type: ignore[arg-type]
    _, other_device = await auth.issue_tokens(user)

    with pytest.raises(CurrentPasswordMismatchError):
        await change_password(
            PasswordUpdate(current_password="wrong", new_password=_NEW_PASSWORD),
            Response(),
            db_session,
            redis,  # type: ignore[arg-type]
            _IP,
            user,
        )

    assert verify_password(_PASSWORD, user.hashed_password)
    # 失敗したときはほかの端末のログインも切らない
    assert await auth.refresh_access_token(other_device)
