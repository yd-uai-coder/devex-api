import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_register_login_and_access_protected_route(client: AsyncClient) -> None:
    """登録→ログイン→保護エンドポイントへのアクセス→リフレッシュ→ログアウトまでの一連の流れを検証する。"""
    register_response = await client.post(
        "/api/v1/auth/register",
        json={"email": "erin@example.com", "password": "s3cret-pass", "full_name": "Erin"},
    )
    assert register_response.status_code == 201

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "erin@example.com", "password": "s3cret-pass"},
    )
    assert login_response.status_code == 200
    tokens = login_response.json()

    me_response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "erin@example.com"

    refresh_response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh_response.status_code == 200
    assert "access_token" in refresh_response.json()

    logout_response = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert logout_response.status_code == 204
