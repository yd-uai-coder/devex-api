"""設計レビューの LLM(構造化出力の呼び出し・失敗の分類・E2E の偽物)のテスト。

SUT は invoke_structured_with_usage・_is_quota_error・unwrap_structured_result・E2eFakeLLM。
ドライバは pytest。スタブは FakeLLM(構造化出力の呼び出しを記録する)だけ。クライアントの組み立ては
tests/unit/test_llm_factory.py、キーが無いときの扱いは tests/unit/test_llm_resolver.py で確かめる。
"""

import anthropic
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.review import review_output

from app.ai.llm.fake import E2eFakeLLM
from app.review import ReviewOutput
from app.services.errors import LLMTokenLimitError
from app.services.llm_retry import _is_quota_error, invoke_structured_with_usage
from app.uml.generation.failures import unwrap_structured_result


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
