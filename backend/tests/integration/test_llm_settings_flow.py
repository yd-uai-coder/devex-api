"""用途ごとのモデルと API キーの登録を、実際の PostgreSQL と Redis で確かめる。

SUT: GET /api/v1/llm/catalog・GET/PUT /api/v1/users/me/models・GET /api/v1/users/me/api-keys・
     PUT/DELETE /api/v1/users/me/api-keys/{provider}(ルート → LlmSettingsService → リポジトリ)
     と、既定値の行の一意(user_id が null の行を NULLS NOT DISTINCT で用途ごとに1行)
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ: キーの確かめ(get_key_verifier)をフェイクに差し替える(外部の provider を呼ばない)。
     暗号の鍵は settings を monkeypatch で替える。利用者は DB に直接作る(登録の上限を避ける)
"""

import uuid
from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from scripts.seed import seed_default_models
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.ai.llm.catalog import Provider
from app.ai.llm.key_check import KeyCheckResult, get_key_verifier
from app.api.deps import get_client_ip
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.user import User
from app.models.user_model_setting import UserModelSetting

pytestmark = pytest.mark.integration

_PASSWORD = "Llm-pass-01"


@pytest.fixture(autouse=True)
def _overrides(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode())
    ip = f"test-{uuid.uuid4().hex}"

    async def accept_known_key(_provider: Provider, key: str) -> KeyCheckResult:
        return KeyCheckResult.VALID if key.startswith("good-") else KeyCheckResult.INVALID

    app.dependency_overrides[get_client_ip] = lambda: ip
    app.dependency_overrides[get_key_verifier] = lambda: accept_known_key
    yield
    app.dependency_overrides.pop(get_client_ip, None)
    app.dependency_overrides.pop(get_key_verifier, None)


async def _login_new_user(client: AsyncClient) -> dict[str, str]:
    email = f"llm-{uuid.uuid4().hex[:8]}@example.com"
    async with AsyncSessionLocal() as session:
        session.add(User(email=email, hashed_password=hash_password(_PASSWORD), user_name="Lin"))
        await session.commit()
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": _PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_register_key_then_choose_model(client: AsyncClient) -> None:
    headers = await _login_new_user(client)

    catalog = await client.get("/api/v1/llm/catalog", headers=headers)
    assert catalog.status_code == 200
    assert len(catalog.json()["models"]) == 8

    choice = {"choices": [{"purpose": "chat", "provider": "claude", "model": "claude-haiku-5-5"}]}
    refused = await client.put("/api/v1/users/me/models", json=choice, headers=headers)
    assert refused.status_code == 422
    assert refused.json()["code"] == "MODEL_PROVIDER_KEY_REQUIRED"

    invalid = await client.put(
        "/api/v1/users/me/api-keys/claude", json={"api_key": "bad-key-0000"}, headers=headers
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "API_KEY_INVALID"

    saved = await client.put(
        "/api/v1/users/me/api-keys/claude", json={"api_key": "good-key-4321"}, headers=headers
    )
    assert saved.status_code == 200
    assert saved.json() == {"provider": "claude", "key_hint": "4321", "needs_reregister": False}

    chosen = await client.put("/api/v1/users/me/models", json=choice, headers=headers)
    assert chosen.status_code == 200
    chat = next(s for s in chosen.json()["settings"] if s["purpose"] == "chat")
    assert (chat["provider"], chat["is_default"], chat["key_missing"]) == ("claude", False, False)

    keys = await client.get("/api/v1/users/me/api-keys", headers=headers)
    assert keys.json()["encryption_available"] is True
    assert "good-key-4321" not in keys.text

    deleted = await client.delete("/api/v1/users/me/api-keys/claude", headers=headers)
    assert deleted.status_code == 204
    after = await client.get("/api/v1/users/me/models", headers=headers)
    chat = next(s for s in after.json()["settings"] if s["purpose"] == "chat")
    assert chat["key_missing"] is True


async def test_unknown_provider_and_missing_encryption_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _login_new_user(client)

    unknown = await client.put(
        "/api/v1/users/me/api-keys/openai", json={"api_key": "good-key-0000"}, headers=headers
    )
    assert unknown.status_code == 422

    monkeypatch.setattr(settings, "ENCRYPTION_KEY", None)
    unavailable = await client.put(
        "/api/v1/users/me/api-keys/gemini", json={"api_key": "good-key-0000"}, headers=headers
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "ENCRYPTION_UNAVAILABLE"
    keys = await client.get("/api/v1/users/me/api-keys", headers=headers)
    assert keys.json() == {"encryption_available": False, "keys": []}


async def test_default_rows_are_unique_per_purpose(client: AsyncClient) -> None:
    del client  # テーブルを作るため
    async with AsyncSessionLocal() as session:
        await seed_default_models(session)
        await seed_default_models(session)
        count = await session.scalar(
            select(func.count())
            .select_from(UserModelSetting)
            .where(UserModelSetting.user_id.is_(None))
        )
        assert count == 4

        session.add(UserModelSetting(user_id=None, purpose="chat", provider="gemini", model="x"))
        with pytest.raises(IntegrityError):
            await session.commit()
