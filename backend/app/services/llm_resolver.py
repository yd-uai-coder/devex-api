"""ユーザーと用途から、呼び出す LLM を決める(マイページの設定 → provider・モデル・キー)。

規則:
- 用途のモデルは、ユーザーの選択(無ければ既定値)。選択の provider のキーが無くなっていたら
  (`key_missing`)既定値に読み替える。
- キーは、今の鍵で復号できる自分のキー。無ければ `.env` の既定のキー。一般ユーザーが既定と違う
  provider を選べるのは自分のキーがあるときだけ(app/services/llm_settings_service.py)なので、
  ここで「既定のキーで別の provider」を呼ぶ経路は生まれない。
- 参考資料(PDF)の取り込みは用途にせず、Gemini で読む(PDF をそのまま渡せるのが Gemini だけ)。
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.catalog import Provider, Purpose
from app.ai.llm.factory import build_llm
from app.core.config import settings
from app.models.user import User
from app.review import policy as review_policy
from app.services.errors import LlmUnavailableError, ReviewUnavailableError
from app.services.llm_defaults import env_default_models
from app.services.llm_settings_service import LlmSettingsService, PurposeSetting


@dataclass(frozen=True)
class ResolvedLlm:
    """解決した LLM。`own_key` は自分のキーで呼ぶか(既定のキーの1日の上限を数えるかに使う)。
    キー自体は持たない(ログ・記録に出さないため)。"""

    llm: Any
    provider: Provider
    model: str
    own_key: bool


class LlmResolver:
    """ユーザーと用途 → LLM の解決。サービスが自分のセッションで作って使う。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = LlmSettingsService(session)

    async def resolve(
        self, user_id: uuid.UUID, purpose: Purpose, *, temperature: float | None = None
    ) -> ResolvedLlm:
        """用途の LLM を返す。キーが自分にも既定にも無ければ、レビュー・判定は
        `ReviewUnavailableError`、チャット・生成は `LlmUnavailableError`。"""
        user = await self._user(user_id)
        provider, model = await self._model_for(user, purpose)
        return await self._build(user, provider, model, purpose, temperature)

    async def model_for(self, user_id: uuid.UUID, purpose: Purpose) -> tuple[Provider, str]:
        """用途で使う provider とモデル(キーは見ない)。呼べなかった回の記録に使う。"""
        return await self._model_for(await self._user(user_id), purpose)

    async def resolve_pdf_reader(self, user_id: uuid.UUID) -> ResolvedLlm:
        """参考資料(PDF)を読む Gemini。生成の用途が Gemini ならそのモデル、違えば既定の Gemini。"""
        user = await self._user(user_id)
        provider, model = await self._model_for(user, Purpose.GENERATION)
        if provider is not Provider.GEMINI:
            model = settings.GEMINI_MODEL
        return await self._build(user, Provider.GEMINI, model, Purpose.GENERATION, None)

    async def review_allowed(self, user: User) -> bool:
        """レビューを使えるアカウントか(superuser、またはレビューの用途の AI の自分のキーが
        ある)。"""
        if user.is_superuser:
            return True
        provider, _ = await self._model_for(user, Purpose.REVIEW)
        own = await self._settings.decrypted_api_key(user, provider)
        return review_policy.review_allowed(is_superuser=False, has_own_review_key=own is not None)

    async def review_effective(self, user: User) -> bool:
        """レビューの実効値(使えるアカウントが設定をオンにしている)。"""
        return review_policy.review_effective(
            review_allowed=await self.review_allowed(user), review_enabled=user.review_enabled
        )

    async def _user(self, user_id: uuid.UUID) -> User:
        user = await self._session.get(User, user_id)
        if user is None:
            raise LlmUnavailableError("ユーザーが見つからないため、AIを呼べません。")
        return user

    async def _model_for(self, user: User, purpose: Purpose) -> tuple[Provider, str]:
        row = _row_for(await self._settings.get_model_settings(user), purpose)
        if row.key_missing:
            return row.default_provider, row.default_model
        return row.provider, row.model

    async def _build(
        self,
        user: User,
        provider: Provider,
        model: str,
        purpose: Purpose,
        temperature: float | None,
    ) -> ResolvedLlm:
        own = await self._settings.decrypted_api_key(user, provider)
        key = own or _env_key(provider)
        if key is None and not settings.E2E_FAKE_LLM:
            if purpose in (Purpose.REVIEW, Purpose.JUDGE):
                raise ReviewUnavailableError("設計レビューのAPIキーが設定されていません。")
            raise LlmUnavailableError(
                "AIのAPIキーが設定されていません。マイページでキーを登録するか、モデルを変えてください。"
            )
        return ResolvedLlm(
            llm=build_llm(provider, model, key, purpose, temperature),
            provider=provider,
            model=model,
            own_key=own is not None,
        )


def injected_llm(llm: Any, purpose: Purpose) -> ResolvedLlm:
    """テストが差し替えた LLM を、`.env` の既定のモデルとして扱う(記録するモデル名のため)。"""
    provider, model = env_default_models(settings)[purpose]
    return ResolvedLlm(llm=llm, provider=provider, model=model, own_key=False)


def _row_for(rows: list[PurposeSetting], purpose: Purpose) -> PurposeSetting:
    return next(row for row in rows if row.purpose is purpose)


def _env_key(provider: Provider) -> str | None:
    """`.env` の既定のキー。"""
    if provider is Provider.GEMINI:
        return settings.GOOGLE_API_KEY
    return settings.ANTHROPIC_API_KEY
