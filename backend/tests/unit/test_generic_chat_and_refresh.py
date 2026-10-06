"""汎用チャットの再試行の共通化と、refresh の寿命の統一のテスト。

SUT: app.services.chat.ChatService.send_message / llm_retry._is_quota_error /
     auth._set_refresh_cookie
ドライバ: 各テスト関数
スタブ: _QuotaWorkflow ── Tavily の利用上限超過を出すワークフロー(LangGraph の代わり)。
        FakeRedis は使わない(bypass_rate_limit=True でレート制限を通さない)。
"""

import uuid
from typing import Any

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession
from tavily import UsageLimitExceededError

from app.api.routes import auth
from app.core.config import settings
from app.models.user import User
from app.services import chat as generic_chat
from app.services import llm_retry
from app.services.errors import LLMQuotaExceededError


class _QuotaWorkflow:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, _state: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        raise UsageLimitExceededError("usage limit exceeded")


def test_tavily_usage_limit_counts_as_quota_error() -> None:
    assert llm_retry._is_quota_error(UsageLimitExceededError("limit")) is True
    assert llm_retry._is_quota_error(RuntimeError("other")) is False


async def test_generic_chat_quota_is_llm_quota_exceeded_without_retry(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _QuotaWorkflow()
    monkeypatch.setattr(generic_chat, "get_chat_workflow", lambda: workflow)
    user = User(email=f"u-{uuid.uuid4()}@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    service = generic_chat.ChatService(db_session, redis=None)  # type: ignore[arg-type]

    with pytest.raises(LLMQuotaExceededError) as raised:
        await service.send_message(
            user_id=user.id, conversation_id=None, message="こんにちは", bypass_rate_limit=True
        )

    assert raised.value.code == "LLM_QUOTA_EXCEEDED"
    assert workflow.calls == 1


def test_refresh_cookie_lives_as_long_as_the_token() -> None:
    response = Response()

    auth._set_refresh_cookie(response, "token")

    assert settings.REFRESH_TOKEN_EXPIRE_DAYS == 14
    cookie = response.headers["set-cookie"]
    assert f"Max-Age={14 * 24 * 3600}" in cookie
