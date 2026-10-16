"""マイページの用途ごとのモデルと、provider ごとの API キーの登録(Phase 68 の解決が読む)。

保存の規則:
- モデルは選べるモデルの表(app/ai/llm/catalog.py)にあるものだけ。
- superuser でないユーザーが既定のキーで使えるのは、既定値と同じ provider だけ。それ以外の
  provider は、自分のキーがあるときだけ選べる。キーを消しても選択の行は消さず、使うときに
  既定値に従う(`key_missing`)。
- キーは保存の前に確かめ(無効は 422、通信の失敗は 502)、暗号化して持つ。鍵が無ければ 503。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.catalog import PURPOSES, Provider, Purpose, find_model
from app.ai.llm.key_check import KeyCheckResult, KeyVerifier
from app.core.config import settings
from app.core.crypto import build_cipher, decrypt_secret, encrypt_secret, encryption_available
from app.models.user import User
from app.repositories.user_api_key import UserApiKeyRepository
from app.repositories.user_model_setting import UserModelSettingRepository
from app.services.errors import (
    ApiKeyCheckFailedError,
    ApiKeyInvalidError,
    DecryptionFailedError,
    ModelNotInCatalogError,
    ModelProviderKeyRequiredError,
)
from app.services.llm_defaults import env_default_models

# 画面に出すキーの末尾の文字数
KEY_HINT_LENGTH = 4


@dataclass(frozen=True)
class PurposeSetting:
    """用途1つの今の設定(app/schemas/llm_settings.py の PurposeSettingRead と同じ形)。"""

    purpose: Purpose
    provider: Provider
    model: str
    is_default: bool
    key_missing: bool
    default_provider: Provider
    default_model: str
    selectable_providers: list[Provider]


@dataclass(frozen=True)
class ApiKeyInfo:
    """登録済みのキー1つ(キー本体は持たない)。"""

    provider: Provider
    key_hint: str
    needs_reregister: bool


class LlmSettingsService:
    """用途ごとのモデルと API キーの読み書き。"""

    def __init__(self, session: AsyncSession, *, verifier: KeyVerifier | None = None) -> None:
        # verifier: キーの確かめ(ルートが app/ai/llm/key_check.py の get_key_verifier で渡す)
        self._session = session
        self._models = UserModelSettingRepository(session)
        self._keys = UserApiKeyRepository(session)
        self._verifier = verifier

    async def get_model_settings(self, user: User) -> list[PurposeSetting]:
        """4用途の今の設定を、カタログの用途の順で返す。"""
        defaults = await self._defaults()
        chosen = {row.purpose: row for row in await self._models.list_for_user(user.id)}
        key_providers = await self._key_providers(user)
        result: list[PurposeSetting] = []
        for info in PURPOSES:
            default_provider, default_model = defaults[info.purpose]
            selectable = _selectable_providers(user, default_provider, key_providers)
            row = chosen.get(info.purpose)
            if row is None:
                provider, model, is_default = default_provider, default_model, True
            else:
                provider, model, is_default = Provider(row.provider), row.model, False
            result.append(
                PurposeSetting(
                    purpose=info.purpose,
                    provider=provider,
                    model=model,
                    is_default=is_default,
                    key_missing=not is_default and provider not in selectable,
                    default_provider=default_provider,
                    default_model=default_model,
                    selectable_providers=selectable,
                )
            )
        return result

    async def update_model_settings(
        self, user: User, choices: list[tuple[Purpose, Provider, str]]
    ) -> list[PurposeSetting]:
        """用途の選択の全体を置き換える(渡さない用途は既定値に従う)。"""
        defaults = await self._defaults()
        key_providers = await self._key_providers(user)
        for purpose, provider, model in choices:
            if find_model(provider, model) is None:
                raise ModelNotInCatalogError(f"選べないモデルです: {provider}/{model}")
            default_provider, _ = defaults[purpose]
            if provider not in _selectable_providers(user, default_provider, key_providers):
                raise ModelProviderKeyRequiredError(
                    f"{provider} のモデルを選ぶには、{provider} の API キーを登録してください。"
                )
        await self._models.replace_for_user(
            user.id, [(str(purpose), str(provider), model) for purpose, provider, model in choices]
        )
        await self._session.commit()
        return await self.get_model_settings(user)

    async def list_api_keys(self, user: User) -> list[ApiKeyInfo]:
        """登録済みのキー(provider の順)。今の鍵で復号できないものは `needs_reregister`。"""
        rows = {row.provider: row for row in await self._keys.list_for_user(user.id)}
        result: list[ApiKeyInfo] = []
        for provider in Provider:
            row = rows.get(provider)
            if row is None:
                continue
            result.append(
                ApiKeyInfo(
                    provider=provider,
                    key_hint=row.key_hint,
                    needs_reregister=not _decryptable(row.encrypted_key),
                )
            )
        return result

    async def register_api_key(self, user: User, provider: Provider, api_key: str) -> ApiKeyInfo:
        """キーを確かめてから暗号化して保存する(同じ provider のキーは置き換える)。"""
        # 鍵が無ければ、外部を呼ぶ前に 503 で断る
        build_cipher(settings.ENCRYPTION_KEY)
        if self._verifier is None:
            raise RuntimeError("キーの確かめの関数が渡されていません。")
        result = await self._verifier(provider, api_key)
        if result is KeyCheckResult.INVALID:
            raise ApiKeyInvalidError("APIキーが正しくありません。")
        if result is KeyCheckResult.UNREACHABLE:
            raise ApiKeyCheckFailedError(
                "APIキーを確かめられませんでした。時間をおいて試してください。"
            )
        hint = api_key[-KEY_HINT_LENGTH:]
        await self._keys.upsert(
            user_id=user.id,
            provider=str(provider),
            encrypted_key=encrypt_secret(api_key),
            key_hint=hint,
        )
        await self._session.commit()
        return ApiKeyInfo(provider=provider, key_hint=hint, needs_reregister=False)

    async def delete_api_key(self, user: User, provider: Provider) -> None:
        """キーを消す(無ければ何もしない)。選択の行は残し、使うときに既定値に従う。"""
        row = await self._keys.get_for_user(user.id, provider)
        if row is not None:
            await self._keys.delete(row)
            await self._session.commit()

    async def _defaults(self) -> dict[Purpose, tuple[Provider, str]]:
        """既定値の行(seed が書く)を読み、無い用途は `.env` の値で補う。"""
        defaults = env_default_models(settings)
        for row in await self._models.list_defaults():
            defaults[Purpose(row.purpose)] = (Provider(row.provider), row.model)
        return defaults

    async def _key_providers(self, user: User) -> set[Provider]:
        """ユーザーが使えるキー(今の鍵で復号できるもの)を登録している provider。"""
        return {
            Provider(row.provider)
            for row in await self._keys.list_for_user(user.id)
            if _decryptable(row.encrypted_key)
        }


def _selectable_providers(
    user: User, default_provider: Provider, key_providers: set[Provider]
) -> list[Provider]:
    """この用途に選べる provider(superuser はすべて。ほかは既定値と同じ provider と、キーのある
    provider)。"""
    return [
        provider
        for provider in Provider
        if user.is_superuser or provider == default_provider or provider in key_providers
    ]


def _decryptable(token: str) -> bool:
    """今の鍵で復号できるか(鍵が無いときも偽)。"""
    if not encryption_available():
        return False
    try:
        decrypt_secret(token)
    except DecryptionFailedError:
        return False
    return True
