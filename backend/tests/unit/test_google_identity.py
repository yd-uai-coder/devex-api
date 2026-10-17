"""Google の ID トークンの検証の部品のテスト。

SUT: GoogleIdTokenVerifier.verify、identity_from_claims、FakeGoogleTokenVerifier.verify、
     get_google_token_verifier
ドライバ: pytest のテスト関数
スタブ: google-auth の `id_token.verify_oauth2_token`(monkeypatch。Google の公開鍵を取りに行かせず、
     返すクレームと送出する例外を決める)。設定は `settings` の属性を monkeypatch で差し替える。
     偽の検証とクレームの読み取りはスタブ不要 ── 純粋で外部を呼ばないため
"""

from typing import Any

import pytest
from google.auth import exceptions as google_exceptions

from app.core.config import settings
from app.services import google_identity
from app.services.errors import (
    GoogleAuthUnavailableError,
    GoogleTokenInvalidError,
    GoogleVerifyFailedError,
)
from app.services.google_identity import (
    FakeGoogleTokenVerifier,
    GoogleIdentity,
    GoogleIdTokenVerifier,
    get_google_token_verifier,
    identity_from_claims,
)

_CLAIMS = {
    "iss": "https://accounts.google.com",
    "sub": "1234567890",
    "email": "gina@example.com",
    "email_verified": True,
    "name": "Gina",
}


def _stub_verify(monkeypatch: pytest.MonkeyPatch, result: Any) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake_verify(token: str, _request: object, audience: str) -> Any:
        calls.append((token, audience))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(google_identity.id_token, "verify_oauth2_token", fake_verify)
    return calls


async def test_verifier_passes_client_id_as_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_verify(monkeypatch, _CLAIMS)

    identity = await GoogleIdTokenVerifier("client-1").verify("token-1")

    assert calls == [("token-1", "client-1")]
    assert identity == GoogleIdentity(
        sub="1234567890", email="gina@example.com", email_verified=True, name="Gina"
    )


@pytest.mark.parametrize(
    "error",
    [ValueError("Token expired"), google_exceptions.GoogleAuthError("Wrong issuer")],
)
async def test_verifier_maps_invalid_tokens_to_401(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _stub_verify(monkeypatch, error)

    with pytest.raises(GoogleTokenInvalidError):
        await GoogleIdTokenVerifier("client-1").verify("token-1")


async def test_verifier_maps_transport_errors_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_verify(monkeypatch, google_exceptions.TransportError("no network"))

    with pytest.raises(GoogleVerifyFailedError):
        await GoogleIdTokenVerifier("client-1").verify("token-1")


def test_claims_without_verified_flag_are_not_verified() -> None:
    identity = identity_from_claims({"sub": "1", "email": "a@example.com"})

    assert identity.email_verified is False
    assert identity.name is None


@pytest.mark.parametrize(
    "claims", [{"email": "a@example.com"}, {"sub": "1"}, {"sub": "", "email": "a@example.com"}]
)
def test_claims_without_sub_or_email_are_rejected(claims: dict[str, Any]) -> None:
    with pytest.raises(GoogleTokenInvalidError):
        identity_from_claims(claims)


async def test_fake_verifier_reads_sub_and_email() -> None:
    identity = await FakeGoogleTokenVerifier().verify("fake:sub-9:e2e.user@example.com")

    assert identity == GoogleIdentity(
        sub="sub-9", email="e2e.user@example.com", email_verified=True, name="e2e.user"
    )


@pytest.mark.parametrize(
    "credential", ["real.jwt.token", "fake::a@example.com", "fake:sub-9:not-an-email", "fake:sub-9"]
)
async def test_fake_verifier_rejects_other_shapes(credential: str) -> None:
    with pytest.raises(GoogleTokenInvalidError):
        await FakeGoogleTokenVerifier().verify(credential)


def test_factory_returns_fake_when_e2e_flag_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "E2E_FAKE_GOOGLE", True)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", None)

    assert isinstance(get_google_token_verifier(), FakeGoogleTokenVerifier)


def test_factory_without_client_id_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "E2E_FAKE_GOOGLE", False)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", None)

    with pytest.raises(GoogleAuthUnavailableError):
        get_google_token_verifier()


def test_factory_returns_real_verifier_with_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "E2E_FAKE_GOOGLE", False)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-1")

    assert isinstance(get_google_token_verifier(), GoogleIdTokenVerifier)
