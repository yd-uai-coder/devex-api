"""パスワードを持たない(Google だけの)利用者のパスワードの設定と、パスワードが要る操作の断りの
テスト。

SUT: UserService.set_password、require_password、ルートの関数(users.set_password)と
     PasswordSet の検証
ドライバ: pytest のテスト関数(ルートの関数を直接呼ぶ。既存のルートのテストと同じ形)
スタブ: FakeRedis(リフレッシュトークンの鍵)。DB はテスト用の SQLite のセッション
"""

import pytest
from fastapi import Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_redis import FakeRedis

from app.api.routes.users import set_password
from app.core.security import decode_token, verify_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import PasswordSet
from app.services.auth import AuthService
from app.services.errors import PasswordAlreadySetError, PasswordNotSetError
from app.services.user import UserService, require_password

_NEW_PASSWORD = "New-pass-02"


async def _google_only(session: AsyncSession) -> User:
    user = await UserRepository(session).create(
        email="gina@example.com", hashed_password=None, user_name="Gina", google_sub="sub-1"
    )
    await session.commit()
    return user


async def test_set_password_gives_google_only_user_a_password(db_session: AsyncSession) -> None:
    user = await _google_only(db_session)

    await UserService(db_session).set_password(user, new_password=_NEW_PASSWORD)

    assert user.hashed_password is not None
    assert verify_password(_NEW_PASSWORD, user.hashed_password)
    # 連携は残る(Google とメールのどちらでもログインできる)
    assert user.google_sub == "sub-1"


async def test_set_password_refuses_user_who_has_one(db_session: AsyncSession) -> None:
    user = await UserService(db_session).create_user(email="p@example.com", password="Old-pass-01")

    with pytest.raises(PasswordAlreadySetError):
        await UserService(db_session).set_password(user, new_password=_NEW_PASSWORD)

    assert user.hashed_password is not None
    assert verify_password("Old-pass-01", user.hashed_password)


async def test_require_password_refuses_only_google_only_user(db_session: AsyncSession) -> None:
    google_only = await _google_only(db_session)
    with_password = await UserService(db_session).create_user(
        email="p@example.com", password="Old-pass-01"
    )

    with pytest.raises(PasswordNotSetError):
        require_password(google_only)
    require_password(with_password)


async def test_set_password_route_cuts_other_devices_and_reissues(
    db_session: AsyncSession,
) -> None:
    user = await _google_only(db_session)
    redis = FakeRedis()
    auth = AuthService(db_session, redis)  # type: ignore[arg-type]
    _, other_device = await auth.issue_tokens(user)
    response = Response()

    token = await set_password(
        PasswordSet(new_password=_NEW_PASSWORD),
        response,
        db_session,
        redis,  # type: ignore[arg-type]
        user,
    )

    assert decode_token(token.access_token)["sub"] == str(user.id)
    claims = decode_token(other_device)
    assert await redis.exists(f"refresh_token:{claims['sub']}:{claims['jti']}") == 0
    new_refresh = response.headers["set-cookie"].split("refresh_token=", 1)[1].split(";", 1)[0]
    assert await auth.refresh_access_token(new_refresh)
    # 設定した後は、メールとパスワードでもログインできる
    assert (await auth.authenticate(email="gina@example.com", password=_NEW_PASSWORD)).id == user.id


@pytest.mark.parametrize("password", ["Sh0rt-a", "nouppercase-01", "NO-LOWER-01", "No-digits-x"])
def test_password_set_applies_the_policy(password: str) -> None:
    with pytest.raises(ValidationError):
        PasswordSet(new_password=password)
