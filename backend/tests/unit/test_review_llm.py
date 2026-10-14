"""設計レビューの LLM(取得・構造化出力の呼び出し・失敗の分類・E2E の偽物)のテスト。

SUT は get_review_llm・invoke_structured_with_usage・_is_quota_error・unwrap_structured_result・
E2eFakeLLM。ドライバは pytest。スタブは FakeLLM(構造化出力の呼び出しを記録する)だけで、
get_review_llm の実クライアントはネットワークを呼ばないコンストラクタなので、そのまま確かめる。
"""

import anthropic
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.review import review_output

from app.ai.llm.claude import get_review_llm
from app.ai.llm.fake import E2eFakeLLM
from app.core.config import settings
from app.review import ReviewOutput
from app.services.errors import LLMTokenLimitError, ReviewUnavailableError
from app.services.llm_retry import _is_quota_error, invoke_structured_with_usage
from app.uml.generation.failures import unwrap_structured_result


@pytest.fixture(autouse=True)
def _clear_review_llm_cache():
    get_review_llm.cache_clear()
    yield
    get_review_llm.cache_clear()


def test_review_llm_is_e2e_fake_when_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", True)

    assert isinstance(get_review_llm(), E2eFakeLLM)


def test_review_llm_without_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)

    with pytest.raises(ReviewUnavailableError):
        get_review_llm()


def test_review_llm_uses_configured_model_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", False)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(settings, "REVIEW_MODEL", "claude-test")
    monkeypatch.setattr(settings, "REVIEW_TIMEOUT_SECONDS", 99.0)

    llm = get_review_llm()

    assert llm.model == "claude-test"
    assert llm.default_request_timeout == 99.0
    # 再試行は invoke_with_retry に任せる
    assert llm.max_retries == 0


async def test_structured_with_usage_passes_method_and_returns_tokens() -> None:
    usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    raw = AIMessage(content="", usage_metadata=usage)  # type: ignore[arg-type]
    output = review_output()
    fake = FakeLLM(structured_sequence=[{"raw": raw, "parsed": output, "parsing_error": None}])

    parsed, got = await invoke_structured_with_usage(
        fake, ReviewOutput, [HumanMessage(content="x")], method="json_schema"
    )

    assert parsed == output
    assert got["input_tokens"] == 100 and got["output_tokens"] == 20
    assert fake.structured_output_methods == ["json_schema"]


def test_anthropic_rate_limit_is_quota_error() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    exc = anthropic.RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=request),  # type: ignore[arg-type]
        body=None,
    )

    assert _is_quota_error(exc)


def test_claude_max_tokens_stop_is_token_limit() -> None:
    raw = AIMessage(content="", response_metadata={"stop_reason": "max_tokens"})

    with pytest.raises(LLMTokenLimitError):
        unwrap_structured_result({"raw": raw, "parsed": None, "parsing_error": None}, ReviewOutput)


async def test_e2e_fake_returns_review_with_hearing_finding() -> None:
    result = await E2eFakeLLM().with_structured_output(
        ReviewOutput, include_raw=True, method="json_schema"
    ).ainvoke([HumanMessage(content="x")])

    findings = result["parsed"].findings
    assert [f.cause for f in findings] == ["design", "hearing"]
    assert findings[1].point_key == "security"
