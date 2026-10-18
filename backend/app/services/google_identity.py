"""Google の ID トークン(Google Identity Services のボタンが返す credential)の検証。

検証は google-auth の `verify_oauth2_token` に任せる(署名・発行者・aud・期限をまとめて確かめる)。
公開鍵を `requests` で取りに行く同期の処理なので、threadpool で呼ぶ。E2E では Google を呼べない
ので、`E2E_FAKE_GOOGLE` のときだけ `fake:<sub>:<email>` の形の偽の credential を受け付ける。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi.concurrency import run_in_threadpool
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.core.config import settings
from app.services.errors import (
    GoogleAuthUnavailableError,
    GoogleTokenInvalidError,
    GoogleVerifyFailedError,
)

_FAKE_PREFIX = "fake"


@dataclass(frozen=True)
class GoogleIdentity:
    """検証済みの ID トークンから取り出した、本人の見分けに使う値。"""

    sub: str
    email: str
    email_verified: bool
    name: str | None = None


class GoogleTokenVerifier(Protocol):
    """credential を検証し、本人の値を返す。不正なら GoogleTokenInvalidError。"""

    async def verify(self, credential: str) -> GoogleIdentity: ...


def identity_from_claims(claims: Mapping[str, Any]) -> GoogleIdentity:
    """検証済みのクレームから GoogleIdentity を作る。sub・email が無ければ不正として扱う。"""
    sub = claims.get("sub")
    email = claims.get("email")
    if not isinstance(sub, str) or not sub or not isinstance(email, str) or not email:
        raise GoogleTokenInvalidError("Google の ID トークンに sub または email がありません")
    name = claims.get("name")
    return GoogleIdentity(
        sub=sub,
        email=email,
        email_verified=claims.get("email_verified") is True,
        name=name if isinstance(name, str) and name else None,
    )


class GoogleIdTokenVerifier:
    """Google の公開鍵で ID トークンを検証する(本物)。"""

    def __init__(self, client_id: str) -> None:
        # client_id: このアプリの OAuth クライアント ID(ID トークンの aud と一致しなければ断る)
        self._client_id = client_id

    async def verify(self, credential: str) -> GoogleIdentity:
        claims = await run_in_threadpool(self._verify_sync, credential)
        return identity_from_claims(claims)

    def _verify_sync(self, credential: str) -> Mapping[str, Any]:
        try:
            return id_token.verify_oauth2_token(
                credential, google_requests.Request(), self._client_id
            )
        except google_exceptions.TransportError as exc:
            # 公開鍵を取りに行けなかった(credential の良し悪しは分からない)
            raise GoogleVerifyFailedError("Google の公開鍵を取得できませんでした") from exc
        except (ValueError, google_exceptions.GoogleAuthError) as exc:
            # 署名・aud・期限が合わない(ValueError)、発行者が Google でない(GoogleAuthError)
            raise GoogleTokenInvalidError("Google の ID トークンが不正です") from exc


class FakeGoogleTokenVerifier:
    """E2E 用の偽の検証。`fake:<sub>:<email>` を受け付け、メールは確認済み、名前はメールの
    ローカル部とする。"""

    async def verify(self, credential: str) -> GoogleIdentity:
        prefix, _, rest = credential.partition(":")
        sub, _, email = rest.partition(":")
        if prefix != _FAKE_PREFIX or not sub or "@" not in email:
            raise GoogleTokenInvalidError("偽の credential の形が正しくありません")
        return GoogleIdentity(
            sub=sub, email=email, email_verified=True, name=email.split("@", 1)[0]
        )


def get_google_token_verifier() -> GoogleTokenVerifier:
    """設定に合った検証を返す。E2E_FAKE_GOOGLE なら偽物、GOOGLE_CLIENT_ID が無ければ 503。"""
    if settings.E2E_FAKE_GOOGLE:
        return FakeGoogleTokenVerifier()
    if not settings.GOOGLE_CLIENT_ID:
        raise GoogleAuthUnavailableError("Google でのログインは、このサーバーでは使えません")
    return GoogleIdTokenVerifier(settings.GOOGLE_CLIENT_ID)
