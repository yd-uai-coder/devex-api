"""マイページからの Google の連携と解除のテスト。

SUT: GoogleAuthService.link_current / unlink、ルートの関数(users.link_google・unlink_google)と
     GoogleLinkCurrentRequest
ドライバ: pytest のテスト関数(ルートの関数を直接呼ぶ。既存のルートのテストと同じ形)
スタブ: FakeGoogleVerifier(credential → 決めた本人の値。Google を呼ばない)、FakeRedis(refresh の
     鍵と試行の回数)。DB はテスト用の SQLite のセッション
"""

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_google import FakeGoogleVerifier
from tests.fixtures.fake_redis import FakeRedis

from app.api.routes.users import link_google, unlink_google
from app.core.config import settings
from app.core.security import decode_token
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import GoogleLinkCurrentRequest
from app.services.auth import AuthService
from app.services.errors import (
    CurrentPasswordMismatchError,
    GoogleAccountConflictError,
    GoogleEmailNotVerifiedError,
    PasswordNotSetError,
    RateLimitExceededError,
)
from app.services.google_auth import GoogleAuthService
from app.services.google_identity import GoogleIdentity
from app.services.user import UserService

_PASSWORD = "Pass-word-01"
_IP = "203.0.113.11"
# アカウントのメール(hana@example.com)と違うメールの Google
_OTHER_MAIL = GoogleIdentity(sub="sub-h", email="hana.private@gmail.com", email_verified=True)


async def _password_user(session: AsyncSession, email: str = "hana@example.com") -> User:
    return await UserService(session).create_user(email=email, password=_PASSWORD)


async def _google_only(session: AsyncSession, sub: str = "sub-g") -> User:
    user = await UserRepository(session).create(
        email="gina@example.com", hashed_password=None, user_name="Gina", google_sub=sub
    )
    await session.commit()
    return user


# --- link_current ---


async def test_link_current_accepts_google_with_a_different_email(
    db_session: AsyncSession,
) -> None:
    user = await _password_user(db_session)

    await GoogleAuthService(db_session).link_current(
        user, _OTHER_MAIL, current_password=_PASSWORD
    )

    assert user.google_sub == "sub-h"
    # アプリのメールは変えない
    assert user.email == "hana@example.com"


async def test_link_current_rejects_wrong_current_password(db_session: AsyncSession) -> None:
    user = await _password_user(db_session)

    with pytest.raises(CurrentPasswordMismatchError):
        await GoogleAuthService(db_session).link_current(
            user, _OTHER_MAIL, current_password="wrong"
        )

    assert user.google_sub is None


async def test_link_current_rejects_unverified_google_email(db_session: AsyncSession) -> None:
    user = await _password_user(db_session)
    unverified = GoogleIdentity(sub="sub-h", email="hana@example.com", email_verified=False)

    with pytest.raises(GoogleEmailNotVerifiedError):
        await GoogleAuthService(db_session).link_current(
            user, unverified, current_password=_PASSWORD
        )


async def test_link_current_rejects_google_linked_to_someone_else(
    db_session: AsyncSession,
) -> None:
    await _google_only(db_session, sub="sub-h")
    user = await _password_user(db_session)

    with pytest.raises(GoogleAccountConflictError):
        await GoogleAuthService(db_session).link_current(
            user, _OTHER_MAIL, current_password=_PASSWORD
        )

    assert user.google_sub is None


async def test_link_current_rejects_user_linked_to_another_google(
    db_session: AsyncSession,
) -> None:
    user = await _password_user(db_session)
    user.google_sub = "sub-old"
    await db_session.commit()

    with pytest.raises(GoogleAccountConflictError):
        await GoogleAuthService(db_session).link_current(
            user, _OTHER_MAIL, current_password=_PASSWORD
        )

    assert user.google_sub == "sub-old"


async def test_link_current_with_the_same_google_again_succeeds(db_session: AsyncSession) -> None:
    user = await _password_user(db_session)
    service = GoogleAuthService(db_session)
    await service.link_current(user, _OTHER_MAIL, current_password=_PASSWORD)

    again = await service.link_current(user, _OTHER_MAIL, current_password=_PASSWORD)

    assert again.google_sub == "sub-h"


async def test_link_current_refuses_google_only_user(db_session: AsyncSession) -> None:
    user = await _google_only(db_session)

    with pytest.raises(PasswordNotSetError):
        await GoogleAuthService(db_session).link_current(user, _OTHER_MAIL, current_password="")


# --- unlink ---


async def test_unlink_clears_the_link_and_keeps_password_login(db_session: AsyncSession) -> None:
    user = await _password_user(db_session)
    user.google_sub = "sub-h"
    await db_session.commit()

    await GoogleAuthService(db_session).unlink(user)

    assert user.google_sub is None
    auth = AuthService(db_session, FakeRedis())  # type: ignore[arg-type]
    assert (await auth.authenticate(email="hana@example.com", password=_PASSWORD)).id == user.id


async def test_unlink_without_link_succeeds(db_session: AsyncSession) -> None:
    user = await _password_user(db_session)

    assert (await GoogleAuthService(db_session).unlink(user)).google_sub is None


async def test_unlink_refuses_google_only_user(db_session: AsyncSession) -> None:
    user = await _google_only(db_session)

    with pytest.raises(PasswordNotSetError):
        await GoogleAuthService(db_session).unlink(user)

    assert user.google_sub == "sub-g"


# --- ルート ---


async def test_link_google_route_links_and_reports_it(db_session: AsyncSession) -> None:
    user = await _password_user(db_session)
    verifier = FakeGoogleVerifier({"cred-h": _OTHER_MAIL})

    read = await link_google(
        GoogleLinkCurrentRequest(credential="cred-h", current_password=_PASSWORD),
        db_session,
        FakeRedis(),  # type: ignore[arg-type]
        _IP,
        user,
        verifier,
    )

    assert (read.google_linked, read.has_password) == (True, True)
    assert verifier.calls == ["cred-h"]


async def test_link_google_route_stops_at_the_google_limit_before_verifying(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GOOGLE_RATE_LIMIT_PER_IP_PER_HOUR", 1)
    user = await _password_user(db_session)
    verifier = FakeGoogleVerifier({"cred-h": _OTHER_MAIL})
    redis = FakeRedis()
    body = GoogleLinkCurrentRequest(credential="cred-h", current_password="wrong")

    with pytest.raises(CurrentPasswordMismatchError):
        await link_google(body, db_session, redis, _IP, user, verifier)  # type: ignore[arg-type]
    with pytest.raises(RateLimitExceededError):
        await link_google(body, db_session, redis, _IP, user, verifier)  # type: ignore[arg-type]

    assert verifier.calls == ["cred-h"]


async def test_link_google_route_counts_current_password_like_login(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_PER_EMAIL_PER_HOUR", 1)
    user = await _password_user(db_session)
    verifier = FakeGoogleVerifier({"cred-h": _OTHER_MAIL})
    redis = FakeRedis()
    body = GoogleLinkCurrentRequest(credential="cred-h", current_password="wrong")

    with pytest.raises(CurrentPasswordMismatchError):
        await link_google(body, db_session, redis, _IP, user, verifier)  # type: ignore[arg-type]
    with pytest.raises(RateLimitExceededError):
        await link_google(body, db_session, redis, _IP, user, verifier)  # type: ignore[arg-type]


async def test_unlink_google_route_cuts_other_devices_and_reissues(
    db_session: AsyncSession,
) -> None:
    user = await _password_user(db_session)
    user.google_sub = "sub-h"
    await db_session.commit()
    redis = FakeRedis()
    auth = AuthService(db_session, redis)  # type: ignore[arg-type]
    _, other_device = await auth.issue_tokens(user)
    response = Response()

    token = await unlink_google(response, db_session, redis, user)  # type: ignore[arg-type]

    assert user.google_sub is None
    assert decode_token(token.access_token)["sub"] == str(user.id)
    claims = decode_token(other_device)
    assert await redis.exists(f"refresh_token:{claims['sub']}:{claims['jti']}") == 0
    new_refresh = response.headers["set-cookie"].split("refresh_token=", 1)[1].split(";", 1)[0]
    assert await auth.refresh_access_token(new_refresh)
