"""マイページのパスワードの設定と、Google の連携・解除を、実際の PostgreSQL と Redis で確かめる。

SUT: POST /api/v1/users/me/password・PUT /api/v1/users/me/email(パスワードが無い人の断り)・
     POST / DELETE /api/v1/users/me/google(ルート → UserService・GoogleAuthService →
     AuthService の失効と出し直し)
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


def _bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def test_google_only_user_sets_password_then_can_change_email(
    client: AsyncClient,
) -> None:
    sub = uuid.uuid4().hex
    email = f"gina-{sub[:8]}@example.com"
    login = await client.post("/api/v1/auth/google", json={"credential": f"fake:{sub}:{email}"})
    headers = _bearer(login.json()["access_token"])

    refused = await client.put(
        "/api/v1/users/me/email",
        json={"new_email": f"new-{email}", "current_password": ""},
        headers=headers,
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "PASSWORD_NOT_SET"

    set_ = await client.post(
        "/api/v1/users/me/password", json={"new_password": _PASSWORD}, headers=headers
    )
    assert set_.status_code == 200
    headers = _bearer(set_.json()["access_token"])
    me = (await client.get("/api/v1/users/me", headers=headers)).json()
    assert (me["has_password"], me["google_linked"]) == (True, True)

    again = await client.post(
        "/api/v1/users/me/password", json={"new_password": _PASSWORD}, headers=headers
    )
    assert again.status_code == 409
    assert again.json()["code"] == "PASSWORD_ALREADY_SET"

    changed = await client.put(
        "/api/v1/users/me/email",
        json={"new_email": f"new-{email}", "current_password": _PASSWORD},
        headers=headers,
    )
    assert changed.status_code == 200
    by_password = await client.post(
        "/api/v1/auth/login", json={"email": f"new-{email}", "password": _PASSWORD}
    )
    assert by_password.status_code == 200


async def test_link_from_mypage_then_unlink_cuts_other_devices(client: AsyncClient) -> None:
    sub = uuid.uuid4().hex
    email = f"hana-{sub[:8]}@example.com"
    async with AsyncSessionLocal() as session:
        session.add(User(email=email, hashed_password=hash_password(_PASSWORD), user_name="Hana"))
        await session.commit()
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": _PASSWORD})
    headers = _bearer(login.json()["access_token"])
    # メールが違う Google を連携する
    credential = f"fake:{sub}:hana-{sub[:8]}@gmail.example"

    wrong = await client.post(
        "/api/v1/users/me/google",
        json={"credential": credential, "current_password": "wrong"},
        headers=headers,
    )
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "CURRENT_PASSWORD_MISMATCH"

    linked = await client.post(
        "/api/v1/users/me/google",
        json={"credential": credential, "current_password": _PASSWORD},
        headers=headers,
    )
    assert linked.status_code == 200
    assert linked.json()["google_linked"] is True

    # Google でログインすると、同じ人に入る(ほかの端末の役)
    by_google = await client.post("/api/v1/auth/google", json={"credential": credential})
    assert by_google.status_code == 200
    other_device = client.cookies.get("refresh_token")

    unlinked = await client.request("DELETE", "/api/v1/users/me/google", headers=headers)
    assert unlinked.status_code == 200
    headers = _bearer(unlinked.json()["access_token"])
    me = (await client.get("/api/v1/users/me", headers=headers)).json()
    assert (me["has_password"], me["google_linked"]) == (True, False)
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200

    # ほかの端末の refresh は失効している
    client.cookies.clear()
    client.cookies.set("refresh_token", other_device or "", path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401

    # 解除した Google は、もう同じ人ではない(だれのメールでもないので新しい利用者になる)
    after = await client.post("/api/v1/auth/google", json={"credential": credential})
    assert after.status_code == 200
    new_me = (await client.get("/api/v1/users/me", headers=_bearer(after.json()["access_token"])))
    assert new_me.json()["id"] != me["id"]


async def test_google_only_user_cannot_unlink(client: AsyncClient) -> None:
    sub = uuid.uuid4().hex
    login = await client.post(
        "/api/v1/auth/google", json={"credential": f"fake:{sub}:g-{sub[:8]}@example.com"}
    )

    response = await client.request(
        "DELETE", "/api/v1/users/me/google", headers=_bearer(login.json()["access_token"])
    )

    assert response.status_code == 409
    assert response.json()["code"] == "PASSWORD_NOT_SET"
