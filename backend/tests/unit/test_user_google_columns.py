"""Google の連携のための `users` の列(google_sub・null 可のパスワード)と、既存の照合の null 対応の
テスト。

SUT: UserRepository.create / get_by_google_sub、AuthService.authenticate、
     UserService.change_email / change_password(パスワードが無い人の断り)、user_read
ドライバ: pytest のテスト関数
スタブ: FakeRedis(AuthService の生成に要るだけ。この観点では使わない)。DB はテスト用の SQLite の
     セッション
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_redis import FakeRedis

from app.api.user_read import user_read
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.auth import AuthService
from app.services.errors import (
    INVALID_CREDENTIALS_MESSAGE,
    InvalidCredentialsError,
    PasswordNotSetError,
)
from app.services.user import UserService

_PASSWORD = "Pass-word-01"


async def _google_only(session: AsyncSession) -> User:
    user = await UserRepository(session).create(
        email="gina@example.com", hashed_password=None, user_name="Gina", google_sub="sub-1"
    )
    await session.commit()
    return user


async def test_get_by_google_sub_finds_only_the_linked_user(db_session: AsyncSession) -> None:
    user = await _google_only(db_session)
    repo = UserRepository(db_session)

    assert await repo.get_by_google_sub("sub-1") is user
    assert await repo.get_by_google_sub("sub-2") is None


async def test_password_login_of_google_only_user_is_the_same_401(
    db_session: AsyncSession,
) -> None:
    await _google_only(db_session)
    auth = AuthService(db_session, FakeRedis())  # type: ignore[arg-type]

    with pytest.raises(InvalidCredentialsError) as exc:
        await auth.authenticate(email="gina@example.com", password=_PASSWORD)

    assert str(exc.value) == INVALID_CREDENTIALS_MESSAGE


async def test_changes_needing_current_password_refuse_google_only_user(
    db_session: AsyncSession,
) -> None:
    user = await _google_only(db_session)
    service = UserService(db_session)

    with pytest.raises(PasswordNotSetError):
        await service.change_email(user, new_email="new@example.com", current_password="")
    with pytest.raises(PasswordNotSetError):
        await service.change_password(user, current_password="", new_password=_PASSWORD)


async def test_user_read_reports_password_and_google_link(db_session: AsyncSession) -> None:
    google_only = await _google_only(db_session)
    with_password = await UserService(db_session).create_user(
        email="pat@example.com", password=_PASSWORD
    )

    google_read = await user_read(db_session, google_only)
    password_read = await user_read(db_session, with_password)

    assert (google_read.has_password, google_read.google_linked) == (False, True)
    assert (password_read.has_password, password_read.google_linked) == (True, False)
    assert "hashed_password" not in google_read.model_dump()
    assert "google_sub" not in google_read.model_dump()
