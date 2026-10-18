"""ユーザーと用途 → LLM の解決のテスト。

SUT: app/services/llm_resolver.py の LlmResolver(resolve・resolve_pdf_reader・review_allowed)と、
     その下で使う LlmSettingsService.decrypted_api_key・app/review/policy.py の review_allowed
ドライバ: pytest のテスト関数
スタブ: 不要 ── LLM のクライアントはコンストラクタがネットワークを呼ばないので実物を作り、どの
     キー・モデルで作られたかをフィールドで確かめる。DB はテスト用の SQLite のセッション、暗号の
     鍵と既定のキーは settings を monkeypatch で替える
"""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.catalog import Provider, Purpose
from app.ai.llm.fake import E2eFakeLLM
from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.models.user import User
from app.repositories.user_api_key import UserApiKeyRepository
from app.review.policy import review_allowed
from app.services.errors import LlmUnavailableError, ReviewUnavailableError
from app.services.llm_resolver import LlmResolver
from app.services.llm_settings_service import LlmSettingsService
from app.services.user import UserService

_OWN_CLAUDE = "sk-ant-own-1234"
_OWN_GEMINI = "own-gemini-5678"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", False)
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", "env-gemini")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "env-claude")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setattr(settings, "REVIEW_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(settings, "JUDGE_MODEL", "claude-haiku-5-5")


async def _user(session: AsyncSession, *, superuser: bool = False) -> User:
    user = await UserService(session).create_user(email="r@example.com", password="pw-123456")
    user.is_superuser = superuser
    await session.commit()
    return user


async def _own_key(session: AsyncSession, user: User, provider: Provider, key: str) -> None:
    await UserApiKeyRepository(session).upsert(
        user_id=user.id,
        provider=str(provider),
        encrypted_key=encrypt_secret(key),
        key_hint=key[-4:],
    )
    await session.commit()


def _key_of(llm) -> str | None:
    """クライアントが持つキー(Gemini・Claude の両方)。"""
    secret = getattr(llm, "anthropic_api_key", None) or getattr(llm, "google_api_key", None)
    return secret.get_secret_value() if secret is not None else None


async def test_defaults_use_env_models_and_env_keys(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    resolver = LlmResolver(db_session)

    chat = await resolver.resolve(user.id, Purpose.CHAT)
    review = await resolver.resolve(user.id, Purpose.REVIEW)

    assert (chat.provider, chat.model, chat.own_key) == (
        Provider.GEMINI,
        "gemini-3.5-flash-lite",
        False,
    )
    assert _key_of(chat.llm) == "env-gemini"
    assert (review.provider, review.model) == (Provider.CLAUDE, "claude-sonnet-5-5")
    assert _key_of(review.llm) == "env-claude"


async def test_own_key_and_choice_are_used(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    await _own_key(db_session, user, Provider.CLAUDE, _OWN_CLAUDE)
    await LlmSettingsService(db_session).update_model_settings(
        user, [(Purpose.GENERATION, Provider.CLAUDE, "claude-haiku-5-5")]
    )

    resolved = await LlmResolver(db_session).resolve(user.id, Purpose.GENERATION)

    assert (resolved.provider, resolved.model, resolved.own_key) == (
        Provider.CLAUDE,
        "claude-haiku-5-5",
        True,
    )
    assert _key_of(resolved.llm) == _OWN_CLAUDE
    # 生成は Claude でも出力の上限を大きく取る
    assert resolved.llm.max_tokens == 16000


async def test_own_key_wins_over_env_key_for_default_provider(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    await _own_key(db_session, user, Provider.GEMINI, _OWN_GEMINI)

    resolved = await LlmResolver(db_session).resolve(user.id, Purpose.CHAT)

    assert resolved.own_key is True
    assert _key_of(resolved.llm) == _OWN_GEMINI


async def test_choice_without_key_falls_back_to_default(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    await _own_key(db_session, user, Provider.CLAUDE, _OWN_CLAUDE)
    await LlmSettingsService(db_session).update_model_settings(
        user, [(Purpose.CHAT, Provider.CLAUDE, "claude-haiku-5-5")]
    )
    await LlmSettingsService(db_session).delete_api_key(user, Provider.CLAUDE)

    resolved = await LlmResolver(db_session).resolve(user.id, Purpose.CHAT)

    assert (resolved.provider, resolved.model, resolved.own_key) == (
        Provider.GEMINI,
        "gemini-3.5-flash-lite",
        False,
    )


async def test_undecryptable_key_is_not_used(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _user(db_session)
    await _own_key(db_session, user, Provider.GEMINI, _OWN_GEMINI)
    # 鍵を替えた(古い鍵を外した)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode())

    resolved = await LlmResolver(db_session).resolve(user.id, Purpose.CHAT)

    assert resolved.own_key is False
    assert _key_of(resolved.llm) == "env-gemini"


async def test_missing_keys_raise_by_purpose(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _user(db_session)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", None)
    resolver = LlmResolver(db_session)

    with pytest.raises(ReviewUnavailableError):
        await resolver.resolve(user.id, Purpose.REVIEW)
    with pytest.raises(ReviewUnavailableError):
        await resolver.resolve(user.id, Purpose.JUDGE)
    with pytest.raises(LlmUnavailableError):
        await resolver.resolve(user.id, Purpose.CHAT)


async def test_e2e_flag_returns_fake_without_keys(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _user(db_session)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", True)

    resolved = await LlmResolver(db_session).resolve(user.id, Purpose.REVIEW)

    assert isinstance(resolved.llm, E2eFakeLLM)
    assert resolved.model == "claude-sonnet-5-5"


async def test_temperature_is_passed_to_client(db_session: AsyncSession) -> None:
    user = await _user(db_session)

    resolved = await LlmResolver(db_session).resolve(user.id, Purpose.CHAT, temperature=0.0)

    assert resolved.llm.temperature == 0.0


async def test_pdf_reader_is_gemini_even_when_generation_is_claude(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, superuser=True)
    resolver = LlmResolver(db_session)
    await LlmSettingsService(db_session).update_model_settings(
        user, [(Purpose.GENERATION, Provider.CLAUDE, "claude-haiku-5-5")]
    )

    claude_generation = await resolver.resolve_pdf_reader(user.id)

    assert (claude_generation.provider, claude_generation.model) == (
        Provider.GEMINI,
        "gemini-3.5-flash-lite",
    )
    await LlmSettingsService(db_session).update_model_settings(
        user, [(Purpose.GENERATION, Provider.GEMINI, "gemini-3.8-flash")]
    )
    assert (await resolver.resolve_pdf_reader(user.id)).model == "gemini-3.8-flash"


async def test_review_allowed_needs_superuser_or_own_review_key(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    resolver = LlmResolver(db_session)

    assert await resolver.review_allowed(user) is False
    await _own_key(db_session, user, Provider.GEMINI, _OWN_GEMINI)
    # レビューの用途は Claude のまま(Gemini のキーでは使えない)
    assert await resolver.review_allowed(user) is False
    await _own_key(db_session, user, Provider.CLAUDE, _OWN_CLAUDE)
    assert await resolver.review_allowed(user) is True

    user.is_superuser = True
    await db_session.commit()
    await LlmSettingsService(db_session).delete_api_key(user, Provider.CLAUDE)
    assert await resolver.review_allowed(user) is True


def test_review_allowed_policy() -> None:
    assert review_allowed(is_superuser=True, has_own_review_key=False) is True
    assert review_allowed(is_superuser=False, has_own_review_key=True) is True
    assert review_allowed(is_superuser=False, has_own_review_key=False) is False
