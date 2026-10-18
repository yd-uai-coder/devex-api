"""用途ごとのモデルの既定値(seed)と、その永続化のテスト。

SUT: scripts/seed.py の seed_default_models、app/services/llm_defaults.py の env_default_models、
     UserModelSettingRepository・UserApiKeyRepository、2つのモデル(UserModelSetting・UserApiKey)
ドライバ: pytest のテスト関数
スタブ: DB はテスト用の SQLite のセッション。`.env` の値は settings を monkeypatch で替える
"""

import pytest
from scripts.seed import seed_default_models
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.catalog import Provider, Purpose
from app.core.config import settings
from app.models import UserApiKey, UserModelSetting
from app.repositories.user_api_key import UserApiKeyRepository
from app.repositories.user_model_setting import UserModelSettingRepository
from app.services.llm_defaults import env_default_models
from app.services.user import UserService


def test_env_default_models_maps_purposes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-3.8-flash")

    defaults = env_default_models(settings)

    assert defaults[Purpose.CHAT] == (Provider.GEMINI, "gemini-3.8-flash")
    assert defaults[Purpose.GENERATION] == (Provider.GEMINI, "gemini-3.8-flash")
    assert defaults[Purpose.REVIEW] == (Provider.CLAUDE, settings.REVIEW_MODEL)
    assert defaults[Purpose.JUDGE] == (Provider.CLAUDE, settings.JUDGE_MODEL)


def test_env_default_models_rejects_model_outside_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "JUDGE_MODEL", "claude-old-model")

    with pytest.raises(ValueError, match="claude-old-model"):
        env_default_models(settings)


async def test_seed_default_models_writes_and_overwrites(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_default_models(db_session)
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-3.6-flash")
    await seed_default_models(db_session)

    rows = await UserModelSettingRepository(db_session).list_defaults()
    count = await db_session.scalar(select(func.count()).select_from(UserModelSetting))

    assert count == 4
    by_purpose = {row.purpose: row for row in rows}
    assert by_purpose["chat"].model == "gemini-3.6-flash"
    assert by_purpose["review"].provider == "claude"


async def test_replace_for_user_drops_unlisted_purposes(db_session: AsyncSession) -> None:
    user = await UserService(db_session).create_user(email="m@example.com", password="pw-123456")
    repo = UserModelSettingRepository(db_session)

    await repo.replace_for_user(
        user.id, [("chat", "gemini", "gemini-3.8-flash"), ("judge", "claude", "claude-haiku-5-5")]
    )
    await repo.replace_for_user(user.id, [("judge", "claude", "claude-opus-5-5")])

    rows = await repo.list_for_user(user.id)
    assert [(r.purpose, r.model) for r in rows] == [("judge", "claude-opus-5-5")]


async def test_api_key_upsert_replaces_same_provider(db_session: AsyncSession) -> None:
    user = await UserService(db_session).create_user(email="k@example.com", password="pw-123456")
    repo = UserApiKeyRepository(db_session)

    await repo.upsert(user_id=user.id, provider="claude", encrypted_key="t1", key_hint="1111")
    await repo.upsert(user_id=user.id, provider="claude", encrypted_key="t2", key_hint="2222")

    rows = await repo.list_for_user(user.id)
    assert len(rows) == 1 and isinstance(rows[0], UserApiKey)
    assert (rows[0].encrypted_key, rows[0].key_hint) == ("t2", "2222")
    assert await repo.get_for_user(user.id, "gemini") is None
