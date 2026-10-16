"""LLM の解決(LlmResolver.resolve)をフェイクの LLM に差し替える部品。

サービスがユーザーと用途から解決する経路を、テストで実 API を呼ばずに通すために使う。どの
ユーザー・用途で解決されたかを `calls` に記録する。
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.ai.llm.catalog import Provider, Purpose
from app.services.llm_resolver import LlmResolver, ResolvedLlm

_DEFAULT_MODELS = {
    Purpose.CHAT: (Provider.GEMINI, "gemini-3.5-flash-lite"),
    Purpose.GENERATION: (Provider.GEMINI, "gemini-3.5-flash-lite"),
    Purpose.REVIEW: (Provider.CLAUDE, "claude-sonnet-5-5"),
    Purpose.JUDGE: (Provider.CLAUDE, "claude-haiku-5-5"),
}


@dataclass
class StubResolver:
    """差し替えた解決。`llms` は用途ごとの LLM(無い用途は `default`)、`models` は用途ごとの
    (provider, モデル)(無い用途は既定)。`error` は送出する例外。"""

    default: Any = None
    llms: dict[Purpose, Any] = field(default_factory=dict)
    models: dict[Purpose, tuple[Provider, str]] = field(default_factory=dict)
    own_key: bool = False
    error: Exception | None = None
    calls: list[tuple[uuid.UUID, Purpose]] = field(default_factory=list)

    async def resolve(
        self, user_id: uuid.UUID, purpose: Purpose, *, temperature: float | None = None
    ) -> ResolvedLlm:
        self.calls.append((user_id, purpose))
        if self.error is not None:
            raise self.error
        provider, model = self.models.get(purpose, _DEFAULT_MODELS[purpose])
        return ResolvedLlm(
            llm=self.llms.get(purpose, self.default),
            provider=provider,
            model=model,
            own_key=self.own_key,
        )


def stub_llm_resolver(
    monkeypatch: pytest.MonkeyPatch, llm: Any = None, **kwargs: Any
) -> StubResolver:
    """`LlmResolver.resolve` を差し替え、記録用の StubResolver を返す。"""
    stub = StubResolver(default=llm, **kwargs)

    async def _resolve(
        _self: LlmResolver,
        user_id: uuid.UUID,
        purpose: Purpose,
        *,
        temperature: float | None = None,
    ) -> ResolvedLlm:
        return await stub.resolve(user_id, purpose, temperature=temperature)

    monkeypatch.setattr(LlmResolver, "resolve", _resolve)
    return stub
