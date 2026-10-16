"""マイページのアカウントの変更を、実際の PostgreSQL と Redis で確かめる。

SUT: PATCH /api/v1/users/me・PUT /api/v1/users/me/email・PUT /api/v1/users/me/password
     (ルート → UserService → AuthService.revoke_all_refresh_tokens の SCAN と削除)
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ: 接続元 IP だけを差し替える(get_client_ip)── ログイン・今のパスワードの試行の IP ごとの
     上限が、スイートの実行を重ねて溜まらないように。利用者は DB に直接作る(登録の上限を避ける)
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

pytestmark = pytest.mark.integration

_PASSWORD = "Old-pass-01"
_NEW_PASSWORD = "New-pass-02"


@pytest.fixture(autouse=True)
def _unique_client_ip() -> Iterator[None]:
    ip = f"test-{uuid.uuid4().hex}"
    app.dependency_overrides[get_client_ip] = lambda: ip
    yield
    app.dependency_overrides.pop(get_client_ip, None)


async def _create_user(email: str) -> None:
    async with AsyncSessionLocal() as session:
        session.add(User(email=email, hashed_password=hash_password(_PASSWORD), user_name="Ivy"))
        await session.commit()


async def _login(client: AsyncClient, email: str, password: str = _PASSWORD) -> str:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


async def test_change_user_name_and_email(client: AsyncClient) -> None:
    email = f"ivy-{uuid.uuid4().hex[:8]}@example.com"
    taken = f"taken-{uuid.uuid4().hex[:8]}@example.com"
    await _create_user(email)
    await _create_user(taken)
    headers = {"Authorization": f"Bearer {await _login(client, email)}"}

    renamed = await client.patch(
        "/api/v1/users/me", json={"user_name": "アイビー"}, headers=headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["user_name"] == "アイビー"

    wrong = await client.put(
        "/api/v1/users/me/email",
        json={"new_email": "new@example.com", "current_password": "wrong"},
        headers=headers,
    )
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "CURRENT_PASSWORD_MISMATCH"

    in_use = await client.put(
        "/api/v1/users/me/email",
        json={"new_email": taken, "current_password": _PASSWORD},
        headers=headers,
    )
    assert in_use.status_code == 409

    new_email = f"ivy-new-{uuid.uuid4().hex[:8]}@example.com"
    changed = await client.put(
        "/api/v1/users/me/email",
        json={"new_email": new_email, "current_password": _PASSWORD},
        headers=headers,
    )
    assert changed.status_code == 200
    me = await client.get("/api/v1/users/me", headers=headers)
    assert me.json()["email"] == new_email


async def test_change_password_revokes_other_sessions(client: AsyncClient) -> None:
    email = f"jay-{uuid.uuid4().hex[:8]}@example.com"
    await _create_user(email)
    await _login(client, email)
    other_device = client.cookies.get("refresh_token")
    access_token = await _login(client, email)
    headers = {"Authorization": f"Bearer {access_token}"}

    weak = await client.put(
        "/api/v1/users/me/password",
        json={"current_password": _PASSWORD, "new_password": "weakpass"},
        headers=headers,
    )
    assert weak.status_code == 422

    changed = await client.put(
        "/api/v1/users/me/password",
        json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
        headers=headers,
    )
    assert changed.status_code == 200
    assert "access_token" in changed.json()

    # 今の端末(応答で出し直した Cookie)は refresh できる
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200

    # ほかの端末の refresh は失効している
    client.cookies.clear()
    client.cookies.set("refresh_token", other_device or "", path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401

    # 新しいパスワードでログインでき、古いパスワードでは入れない
    old = await client.post("/api/v1/auth/login", json={"email": email, "password": _PASSWORD})
    assert old.status_code == 401
    await _login(client, email, _NEW_PASSWORD)
