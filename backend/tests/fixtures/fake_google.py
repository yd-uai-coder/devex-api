"""Google の ID トークンの検証のフェイク。credential をキーに、決めた GoogleIdentity を返す
(知らない credential は不正として 401)。ルートの関数への引数と、integration の
dependency_overrides の両方で使う。"""

from __future__ import annotations

from app.services.errors import GoogleTokenInvalidError
from app.services.google_identity import GoogleIdentity


class FakeGoogleVerifier:
    """`GoogleTokenVerifier` の代わり。受け取った credential を記録する。"""

    def __init__(self, identities: dict[str, GoogleIdentity] | None = None) -> None:
        self._identities = dict(identities or {})
        self.calls: list[str] = []

    def add(self, credential: str, identity: GoogleIdentity) -> None:
        self._identities[credential] = identity

    async def verify(self, credential: str) -> GoogleIdentity:
        self.calls.append(credential)
        identity = self._identities.get(credential)
        if identity is None:
            raise GoogleTokenInvalidError("unknown credential")
        return identity
