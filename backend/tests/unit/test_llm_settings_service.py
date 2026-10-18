"""用途ごとのモデルと API キーの登録(サービスとルート)のテスト。

SUT: LlmSettingsService(get_model_settings・update_model_settings・list_api_keys・
     register_api_key・delete_api_key)、ルートの関数(users.read_model_settings・
     update_model_settings・read_api_keys・register_api_key・delete_api_key、
     llm.read_llm_catalog)と ModelSettingsUpdate・ApiKeyUpdate の検証
ドライバ: pytest のテスト関数(ルートの関数を直接呼ぶ。既存のルートのテストと同じ形)
スタブ: キーの確かめはフェイクの関数(結果を決めて返す)。FakeRedis(試行の回数)。
     DB はテスト用の SQLite のセッション。暗号の鍵は settings を monkeypatch で替える
"""

from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_redis import FakeRedis

from app.ai.llm.catalog import Provider, Purpose
from app.ai.llm.key_check import KeyCheckResult, KeyVerifier
from app.api.routes.llm import read_llm_catalog
from app.api.routes.users import (
    delete_api_key,
    read_api_keys,
    read_model_settings,
    register_api_key,
    update_model_settings,
)
from app.core.config import settings
from app.models.user import User
from app.schemas.llm_settings import ApiKeyUpdate, ModelChoice, ModelSettingsUpdate
from app.services.errors import (
    ApiKeyCheckFailedError,
    ApiKeyInvalidError,
    EncryptionUnavailableError,
    ModelNotInCatalogError,
    ModelProviderKeyRequiredError,
    RateLimitExceededError,
)
from app.services.llm_settings_service import LlmSettingsService
from app.services.user import UserService

_CLAUDE_KEY = "sk-ant-test-key-9876"


def _verifier(result: KeyCheckResult, calls: list[Provider] | None = None) -> KeyVerifier:
    async def verify(provider: Provider, _key: str) -> KeyCheckResult:
        if calls is not None:
            calls.append(provider)
        return result

    return verify


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode())


async def _user(session: AsyncSession, *, superuser: bool = False) -> User:
    user = await UserService(session).create_user(email="u@example.com", password="pw-123456")
    user.is_superuser = superuser
    await session.commit()
    return user


def _by_purpose(rows: list) -> dict:
    return {row.purpose: row for row in rows}


async def test_defaults_come_from_env_when_no_rows(db_session: AsyncSession) -> None:
    user = await _user(db_session)

    rows = _by_purpose(await LlmSettingsService(db_session).get_model_settings(user))

    assert list(rows) == list(Purpose)
    assert rows[Purpose.CHAT].is_default is True
    assert rows[Purpose.CHAT].model == settings.GEMINI_MODEL
    assert rows[Purpose.REVIEW].provider is Provider.CLAUDE
    # 一般ユーザーが既定のキーで選べるのは既定値と同じ provider だけ
    assert rows[Purpose.CHAT].selectable_providers == [Provider.GEMINI]
    assert rows[Purpose.JUDGE].selectable_providers == [Provider.CLAUDE]


async def test_non_superuser_needs_own_key_for_other_provider(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    service = LlmSettingsService(db_session, verifier=_verifier(KeyCheckResult.VALID))

    with pytest.raises(ModelProviderKeyRequiredError) as exc_info:
        await service.update_model_settings(
            user, [(Purpose.CHAT, Provider.CLAUDE, "claude-haiku-5-5")]
        )
    assert exc_info.value.status_code == 422

    await service.register_api_key(user, Provider.CLAUDE, _CLAUDE_KEY)
    rows = _by_purpose(
        await service.update_model_settings(
            user, [(Purpose.CHAT, Provider.CLAUDE, "claude-haiku-5-5")]
        )
    )

    assert rows[Purpose.CHAT].model == "claude-haiku-5-5"
    assert rows[Purpose.CHAT].is_default is False
    assert rows[Purpose.GENERATION].is_default is True


async def test_superuser_can_pick_any_provider(db_session: AsyncSession) -> None:
    user = await _user(db_session, superuser=True)

    rows = _by_purpose(
        await LlmSettingsService(db_session).update_model_settings(
            user, [(Purpose.REVIEW, Provider.GEMINI, "gemini-3.8-flash")]
        )
    )

    assert rows[Purpose.REVIEW].provider is Provider.GEMINI
    assert rows[Purpose.REVIEW].selectable_providers == [Provider.GEMINI, Provider.CLAUDE]


async def test_model_outside_catalog_is_rejected(db_session: AsyncSession) -> None:
    user = await _user(db_session, superuser=True)

    with pytest.raises(ModelNotInCatalogError):
        await LlmSettingsService(db_session).update_model_settings(
            user, [(Purpose.CHAT, Provider.GEMINI, "claude-haiku-5-5")]
        )


async def test_deleting_key_marks_choice_as_key_missing(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    service = LlmSettingsService(db_session, verifier=_verifier(KeyCheckResult.VALID))
    await service.register_api_key(user, Provider.CLAUDE, _CLAUDE_KEY)
    await service.update_model_settings(
        user, [(Purpose.CHAT, Provider.CLAUDE, "claude-sonnet-5-5")]
    )

    await service.delete_api_key(user, Provider.CLAUDE)
    await service.delete_api_key(user, Provider.CLAUDE)  # 無くても何もしない
    rows = _by_purpose(await service.get_model_settings(user))

    # 選択の行は残し、使うときは既定値に従う
    assert rows[Purpose.CHAT].model == "claude-sonnet-5-5"
    assert rows[Purpose.CHAT].key_missing is True


async def test_register_stores_encrypted_key_and_hint(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    calls: list[Provider] = []
    service = LlmSettingsService(db_session, verifier=_verifier(KeyCheckResult.VALID, calls))

    info = await service.register_api_key(user, Provider.CLAUDE, _CLAUDE_KEY)
    keys = await service.list_api_keys(user)

    assert calls == [Provider.CLAUDE]
    assert info.key_hint == "9876"
    assert [(k.provider, k.key_hint, k.needs_reregister) for k in keys] == [
        (Provider.CLAUDE, "9876", False)
    ]


@pytest.mark.parametrize(
    ("result", "error", "status"),
    [
        (KeyCheckResult.INVALID, ApiKeyInvalidError, 422),
        (KeyCheckResult.UNREACHABLE, ApiKeyCheckFailedError, 502),
    ],
)
async def test_register_failure_does_not_save(
    db_session: AsyncSession, result: KeyCheckResult, error: type[Exception], status: int
) -> None:
    user = await _user(db_session)
    service = LlmSettingsService(db_session, verifier=_verifier(result))

    with pytest.raises(error) as exc_info:
        await service.register_api_key(user, Provider.GEMINI, "AIza-test-key")

    assert exc_info.value.status_code == status  # type: ignore[attr-defined]
    assert await service.list_api_keys(user) == []


async def test_register_without_encryption_key_is_503_before_checking(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _user(db_session)
    calls: list[Provider] = []
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", None)

    with pytest.raises(EncryptionUnavailableError):
        await LlmSettingsService(
            db_session, verifier=_verifier(KeyCheckResult.VALID, calls)
        ).register_api_key(user, Provider.CLAUDE, _CLAUDE_KEY)
    assert calls == []


async def test_key_from_dropped_encryption_key_needs_reregister(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _user(db_session)
    service = LlmSettingsService(db_session, verifier=_verifier(KeyCheckResult.VALID))
    await service.register_api_key(user, Provider.CLAUDE, _CLAUDE_KEY)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode())

    keys = await service.list_api_keys(user)
    rows = _by_purpose(await service.get_model_settings(user))

    assert keys[0].needs_reregister is True
    # 復号できないキーは、選べる provider の根拠にしない
    assert rows[Purpose.CHAT].selectable_providers == [Provider.GEMINI]


def test_update_body_rejects_duplicate_purposes() -> None:
    choice = ModelChoice(purpose=Purpose.CHAT, provider=Provider.GEMINI, model="gemini-3.6-flash")
    with pytest.raises(ValidationError):
        ModelSettingsUpdate(choices=[choice, choice])
    with pytest.raises(ValidationError):
        ApiKeyUpdate(api_key="   short ")


async def test_routes_round_trip(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    redis: Any = FakeRedis()
    verifier = _verifier(KeyCheckResult.VALID)

    catalog = await read_llm_catalog(user)
    registered = await register_api_key(
        Provider.CLAUDE, ApiKeyUpdate(api_key=f"  {_CLAUDE_KEY} "), db_session, redis, user,
        verifier,
    )
    keys = await read_api_keys(db_session, user)
    updated = await update_model_settings(
        ModelSettingsUpdate(
            choices=[
                ModelChoice(
                    purpose=Purpose.JUDGE, provider=Provider.CLAUDE, model="claude-haiku-5-5"
                )
            ]
        ),
        db_session,
        user,
    )
    await delete_api_key(Provider.CLAUDE, db_session, user)
    after = await read_model_settings(db_session, user)

    assert len(catalog.models) == 8 and len(catalog.purposes) == 4
    assert registered.key_hint == "9876"
    assert keys.encryption_available is True and keys.keys[0].provider is Provider.CLAUDE
    assert {s.purpose: s.is_default for s in updated.settings}[Purpose.JUDGE] is False
    # 判定の既定値は Claude なので、キーを消しても一般ユーザーが選べる(key_missing にならない)
    assert {s.purpose: s.key_missing for s in after.settings}[Purpose.JUDGE] is False


async def test_register_route_counts_attempts(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _user(db_session)
    redis: Any = FakeRedis()
    monkeypatch.setattr(settings, "API_KEY_RATE_LIMIT_PER_HOUR", 1)
    verifier = _verifier(KeyCheckResult.INVALID)

    with pytest.raises(ApiKeyInvalidError):
        await register_api_key(
            Provider.GEMINI, ApiKeyUpdate(api_key="AIza-bad-key"), db_session, redis, user,
            verifier,
        )
    with pytest.raises(RateLimitExceededError):
        await register_api_key(
            Provider.GEMINI, ApiKeyUpdate(api_key="AIza-bad-key"), db_session, redis, user,
            verifier,
        )
