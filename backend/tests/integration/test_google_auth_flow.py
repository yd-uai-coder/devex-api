"""Google でのログイン・連携の確かめを、実際の PostgreSQL と Redis で確かめる。

SUT: POST /api/v1/auth/google・POST /api/v1/auth/google/link(ルート → GoogleAuthService →
     AuthService.issue_tokens と Cookie)、GET /api/v1/users/me の has_password・google_linked
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ: ID トークンの検証を E2E 用の偽物(FakeGoogleTokenVerifier。`fake:<sub>:<email>`)に差し替える
     (Google を呼ばない)。接続元 IP も差し替える(上限がスイートの実行を重ねて溜まらないように)。
     パスワードの利用者は DB に直接作る(登録の上限を避ける)
"""

import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient

from app.api.deps import get_client_ip
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.user import User
from app.services.google_identity import FakeGoogleTokenVerifier, get_google_token_verifier

pytestmark = pytest.mark.integration

_PASSWORD = "Pass-word-01"


@pytest.fixture(autouse=True)
def _fake_google_and_unique_ip() -> Iterator[None]:
    ip = f"test-{uuid.uuid4().hex}"
    app.dependency_overrides[get_client_ip] = lambda: ip
    app.dependency_overrides[get_google_token_verifier] = FakeGoogleTokenVerifier
    yield
    app.dependency_overrides.pop(get_client_ip, None)
    app.dependency_overrides.pop(get_google_token_verifier, None)


async def _me(client: AsyncClient, access_token: str) -> dict[str, object]:
    response = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    return response.json()


async def test_google_login_registers_and_logs_in_again(client: AsyncClient) -> None:
    sub = uuid.uuid4().hex
    email = f"gina-{sub[:8]}@example.com"
    credential = f"fake:{sub}:{email}"

    first = await client.post("/api/v1/auth/google", json={"credential": credential})
    assert first.status_code == 200
    assert "refresh_token" in first.cookies
    me = await _me(client, first.json()["access_token"])
    assert (me["email"], me["has_password"], me["google_linked"]) == (email, False, True)

    again = await client.post("/api/v1/auth/google", json={"credential": credential})
    assert again.status_code == 200
    assert (await _me(client, again.json()["access_token"]))["id"] == me["id"]

    # パスワードを持たないので、パスワードのログインは違うときと同じ 401
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": _PASSWORD})
    assert login.status_code == 401


async def test_existing_password_user_links_once_then_both_logins_work(
    client: AsyncClient,
) -> None:
    sub = uuid.uuid4().hex
    email = f"pat-{sub[:8]}@example.com"
    async with AsyncSessionLocal() as session:
        session.add(User(email=email, hashed_password=hash_password(_PASSWORD), user_name="Pat"))
        await session.commit()
    credential = f"fake:{sub}:{email}"

    required = await client.post("/api/v1/auth/google", json={"credential": credential})
    assert required.status_code == 409
    assert required.json()["code"] == "GOOGLE_LINK_REQUIRED"

    wrong = await client.post(
        "/api/v1/auth/google/link", json={"credential": credential, "password": "wrong"}
    )
    assert wrong.status_code == 401

    linked = await client.post(
        "/api/v1/auth/google/link", json={"credential": credential, "password": _PASSWORD}
    )
    assert linked.status_code == 200
    me = await _me(client, linked.json()["access_token"])
    assert (me["has_password"], me["google_linked"]) == (True, True)

    by_google = await client.post("/api/v1/auth/google", json={"credential": credential})
    by_password = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": _PASSWORD}
    )
    assert by_google.status_code == 200
    assert by_password.status_code == 200

    other = await client.post(
        "/api/v1/auth/google", json={"credential": f"fake:{uuid.uuid4().hex}:{email}"}
    )
    assert other.status_code == 409
    assert other.json()["code"] == "GOOGLE_ACCOUNT_CONFLICT"


async def test_google_login_without_client_id_is_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides.pop(get_google_token_verifier, None)
    monkeypatch.setattr(settings, "E2E_FAKE_GOOGLE", False)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", None)

    response = await client.post("/api/v1/auth/google", json={"credential": "x"})

    assert response.status_code == 503
    assert response.json()["code"] == "GOOGLE_AUTH_UNAVAILABLE"
