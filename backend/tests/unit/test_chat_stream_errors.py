"""ヒアリングチャットのSSEで、途中の失敗を`event: error`で伝えることのテスト。

SUT: send_hearing_message(ルート)と llm_retry.as_llm_error
ドライバ: 各テスト関数(StreamingResponseの本体を最後まで読む)
スタブ: _FailingStreamLLM ── 1断片を返した後に例外を出すLLM(途中で切れるストリームを模す)。
返信の前の完了判定には「足りない」を返す(返信のストリームまで進めるため)。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.uml import create_project

from app.api.routes.projects import send_hearing_message
from app.repositories.chat_history import ChatHistoryRepository
from app.schemas.generation import HearingCompletionCheck
from app.schemas.hearing import HearingMessageRequest
from app.services import chat_service
from app.services.errors import GenerationFailedError, LLMQuotaExceededError
from app.services.llm_retry import as_llm_error


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FailingStreamLLM:
    async def astream(self, _messages: Any) -> AsyncIterator[_Chunk]:
        yield _Chunk("途中まで")
        raise RuntimeError("connection reset")

    def with_structured_output(self, schema: Any) -> Any:
        insufficient = HearingCompletionCheck(is_sufficient=False, summary="", missing_points=[])
        return FakeLLM(structured=insufficient).with_structured_output(schema)


async def _read_body(response: Any) -> str:
    parts = [
        part if isinstance(part, str) else part.decode() async for part in response.body_iterator
    ]
    return "".join(parts)


async def test_stream_failure_is_sent_as_error_event(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_service, "get_gemini_llm", lambda: _FailingStreamLLM())
    project = await create_project(db_session)
    await db_session.commit()
    project_id = project.id  # rollbackで失効した後に読まないよう、先に取り出す

    response = await send_hearing_message(
        HearingMessageRequest(message="こんにちは"), db_session, project
    )
    body = await _read_body(response)

    assert "途中まで" in body
    assert "[DONE]" not in body
    event = body.split("\n\n")[-2]
    assert event.startswith("event: error\n")
    payload = json.loads(event.split("data: ", 1)[1])
    assert payload["code"] == "LLM_API_ERROR"
    # 失敗した発言は残さない(rollback)
    assert await ChatHistoryRepository(db_session).list_for_project(project_id) == []


def test_as_llm_error_keeps_app_errors_and_wraps_others() -> None:
    quota = LLMQuotaExceededError("上限")

    assert as_llm_error(quota) is quota
    assert isinstance(as_llm_error(RuntimeError("x")), GenerationFailedError)
