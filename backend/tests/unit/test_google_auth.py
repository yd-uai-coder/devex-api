"""Google でのログイン・新規の登録・既存のアカウントとの連携のテスト。

SUT: GoogleAuthService.login_or_register / link、ルートの関数(auth.google_login・google_link)
ドライバ: pytest のテスト関数(ルートの関数を直接呼ぶ。既存のルートのテストと同じ形)
スタブ: FakeGoogleVerifier(credential → 決めた本人の値。Google を呼ばない)、FakeRedis(refresh の
     鍵と試行の回数)。DB はテスト用の SQLite のセッション
"""

from typing import cast

import pytest
from fastapi import Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_google import FakeGoogleVerifier
from tests.fixtures.fake_redis import FakeRedis

from app.api.routes.auth import google_link, google_login
from app.core.config import settings
from app.core.security import decode_token
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import GoogleLinkRequest, GoogleLoginRequest
from app.services.auth import AuthService
from app.services.errors import (
    GoogleAccountConflictError,
    GoogleEmailNotVerifiedError,
    GoogleLinkRequiredError,
    GoogleTokenInvalidError,
    InvalidCredentialsError,
    RateLimitExceededError,
)
from app.services.google_auth import GoogleAuthService
from app.services.google_identity import GoogleIdentity
from app.services.user import UserService

_PASSWORD = "Pass-word-01"
_IP = "203.0.113.9"
_GINA = GoogleIdentity(sub="sub-gina", email="gina@example.com", email_verified=True, name="Gina")


async def _password_user(session: AsyncSession, email: str = "gina@example.com") -> User:
    return await UserService(session).create_user(email=email, password=_PASSWORD, user_name="P")


# --- login_or_register ---


async def test_new_email_registers_a_google_only_user(db_session: AsyncSession) -> None:
    user = await GoogleAuthService(db_session).login_or_register(_GINA)

    assert (user.email, user.user_name, user.google_sub) == ("gina@example.com", "Gina", "sub-gina")
    assert user.hashed_password is None
    assert user.is_superuser is False


async def test_long_name_is_cut_to_the_column_length(db_session: AsyncSession) -> None:
    identity = GoogleIdentity(
        sub="s", email="long@example.com", email_verified=True, name="x" * 300
    )

    user = await GoogleAuthService(db_session).login_or_register(identity)

    assert user.user_name == "x" * 255


async def test_linked_user_is_found_by_sub_even_if_google_email_changed(
    db_session: AsyncSession,
) -> None:
    first = await GoogleAuthService(db_session).login_or_register(_GINA)
    moved = GoogleIdentity(sub="sub-gina", email="gina.new@example.com", email_verified=True)

    again = await GoogleAuthService(db_session).login_or_register(moved)

    assert again.id == first.id
    assert again.email == "gina@example.com"


async def test_same_email_without_link_requires_password_link(db_session: AsyncSession) -> None:
    await _password_user(db_session)

    with pytest.raises(GoogleLinkRequiredError):
        await GoogleAuthService(db_session).login_or_register(_GINA)


async def test_same_email_linked_to_another_google_is_conflict(db_session: AsyncSession) -> None:
    await GoogleAuthService(db_session).login_or_register(_GINA)
    other = GoogleIdentity(sub="sub-other", email="gina@example.com", email_verified=True)

    with pytest.raises(GoogleAccountConflictError):
        await GoogleAuthService(db_session).login_or_register(other)


async def test_unverified_email_is_rejected(db_session: AsyncSession) -> None:
    identity = GoogleIdentity(sub="s", email="u@example.com", email_verified=False)

    with pytest.raises(GoogleEmailNotVerifiedError):
        await GoogleAuthService(db_session).login_or_register(identity)
    assert await UserRepository(db_session).get_by_email("u@example.com") is None


async def test_inactive_linked_user_is_401(db_session: AsyncSession) -> None:
    user = await GoogleAuthService(db_session).login_or_register(_GINA)
    user.is_active = False
    await db_session.commit()

    with pytest.raises(InvalidCredentialsError):
        await GoogleAuthService(db_session).login_or_register(_GINA)


# --- link ---


async def test_link_with_password_sets_sub_and_keeps_password(db_session: AsyncSession) -> None:
    user = await _password_user(db_session)

    linked = await GoogleAuthService(db_session).link(_GINA, password=_PASSWORD)

    assert linked.id == user.id
    assert linked.google_sub == "sub-gina"
    assert linked.hashed_password is not None
    # 連携の後は、Google でもパスワードでも入れる
    again = await GoogleAuthService(db_session).login_or_register(_GINA)
    assert again.id == user.id
    auth = AuthService(db_session, cast(Redis, FakeRedis()))
    assert (await auth.authenticate(email="gina@example.com", password=_PASSWORD)).id == user.id


@pytest.mark.parametrize("password", ["wrong", ""])
async def test_link_with_wrong_password_is_401(db_session: AsyncSession, password: str) -> None:
    await _password_user(db_session)

    with pytest.raises(InvalidCredentialsError):
        await GoogleAuthService(db_session).link(_GINA, password=password)


async def test_link_without_account_or_password_is_the_same_401(db_session: AsyncSession) -> None:
    service = GoogleAuthService(db_session)
    with pytest.raises(InvalidCredentialsError):
        await service.link(_GINA, password=_PASSWORD)

    await service.login_or_register(_GINA)  # Google だけの利用者(パスワードを持たない)
    other = GoogleIdentity(sub="sub-other", email="gina@example.com", email_verified=True)
    with pytest.raises(InvalidCredentialsError):
        await service.link(other, password=_PASSWORD)


async def test_link_is_conflict_when_either_side_is_linked_elsewhere(
    db_session: AsyncSession,
) -> None:
    service = GoogleAuthService(db_session)
    await _password_user(db_session)
    await service.link(_GINA, password=_PASSWORD)
    # このアカウントは別の Google と連携済み
    other_google = GoogleIdentity(sub="sub-other", email="gina@example.com", email_verified=True)
    with pytest.raises(GoogleAccountConflictError):
        await service.link(other_google, password=_PASSWORD)

    # この Google アカウントはほかの利用者と連携済み
    await _password_user(db_session, email="pat@example.com")
    same_google = GoogleIdentity(sub="sub-gina", email="pat@example.com", email_verified=True)
    with pytest.raises(GoogleAccountConflictError):
        await service.link(same_google, password=_PASSWORD)


async def test_link_again_with_the_same_google_returns_the_user(db_session: AsyncSession) -> None:
    await _password_user(db_session)
    service = GoogleAuthService(db_session)
    first = await service.link(_GINA, password=_PASSWORD)

    assert (await service.link(_GINA, password=_PASSWORD)).id == first.id


async def test_link_rejects_unverified_email(db_session: AsyncSession) -> None:
    await _password_user(db_session)
    identity = GoogleIdentity(sub="s", email="gina@example.com", email_verified=False)

    with pytest.raises(GoogleEmailNotVerifiedError):
        await GoogleAuthService(db_session).link(identity, password=_PASSWORD)


# --- ルート ---


async def test_google_login_route_issues_access_and_refresh_cookie(
    db_session: AsyncSession,
) -> None:
    redis = FakeRedis()
    verifier = FakeGoogleVerifier({"cred-gina": _GINA})
    response = Response()

    token = await google_login(
        GoogleLoginRequest(credential="cred-gina"),
        response,
        db_session,
        cast(Redis, redis),
        _IP,
        verifier,
    )

    user = await UserRepository(db_session).get_by_google_sub("sub-gina")
    assert user is not None
    assert decode_token(token.access_token)["sub"] == str(user.id)
    assert "refresh_token=" in response.headers["set-cookie"]
    assert verifier.calls == ["cred-gina"]


async def test_google_login_route_rejects_unknown_credential(db_session: AsyncSession) -> None:
    with pytest.raises(GoogleTokenInvalidError):
        await google_login(
            GoogleLoginRequest(credential="forged"),
            Response(),
            db_session,
            cast(Redis, FakeRedis()),
            _IP,
            FakeGoogleVerifier(),
        )


async def test_google_routes_are_limited_per_ip(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GOOGLE_RATE_LIMIT_PER_IP_PER_HOUR", 1)
    redis = cast(Redis, FakeRedis())
    verifier = FakeGoogleVerifier({"cred-gina": _GINA})
    body = GoogleLoginRequest(credential="cred-gina")

    await google_login(body, Response(), db_session, redis, _IP, verifier)
    with pytest.raises(RateLimitExceededError):
        await google_login(body, Response(), db_session, redis, _IP, verifier)
    # 数えた後は検証を呼ばない
    assert verifier.calls == ["cred-gina"]


async def test_google_link_route_counts_password_tries_like_login(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_PER_EMAIL_PER_HOUR", 1)
    await _password_user(db_session)
    redis = cast(Redis, FakeRedis())
    verifier = FakeGoogleVerifier({"cred-gina": _GINA})

    with pytest.raises(InvalidCredentialsError):
        await google_link(
            GoogleLinkRequest(credential="cred-gina", password="wrong"),
            Response(),
            db_session,
            redis,
            _IP,
            verifier,
        )
    with pytest.raises(RateLimitExceededError):
        await google_link(
            GoogleLinkRequest(credential="cred-gina", password=_PASSWORD),
            Response(),
            db_session,
            redis,
            _IP,
            verifier,
        )


async def test_google_link_route_links_and_issues_tokens(db_session: AsyncSession) -> None:
    user = await _password_user(db_session)
    response = Response()

    token = await google_link(
        GoogleLinkRequest(credential="cred-gina", password=_PASSWORD),
        response,
        db_session,
        cast(Redis, FakeRedis()),
        _IP,
        FakeGoogleVerifier({"cred-gina": _GINA}),
    )

    assert decode_token(token.access_token)["sub"] == str(user.id)
    assert user.google_sub == "sub-gina"
    assert "refresh_token=" in response.headers["set-cookie"]
