import pytest
from langchain_core.messages import AIMessage
from tests.fixtures.uml import component_output

from app.services.errors import (
    GenerationFailedError,
    LLMInvalidOutputError,
    LLMQuotaExceededError,
    LLMTokenLimitError,
)
from app.uml.generation import (
    ComponentGenerationOutput,
    classify_failure,
    unwrap_structured_result,
)

# スタブ不要 ── 分類・解釈はいずれも例外/dictを受け取って値を返す純粋関数で、LLMを呼ばない。


def _raw(finish_reason: str) -> AIMessage:
    return AIMessage(content="", response_metadata={"finish_reason": finish_reason})


def test_unwrap_returns_parsed_output() -> None:
    parsed = component_output()
    result = {"raw": _raw("STOP"), "parsed": parsed, "parsing_error": None}

    assert unwrap_structured_result(result, ComponentGenerationOutput) is parsed


def test_unwrap_raises_token_limit_when_output_was_cut_off() -> None:
    result = {"raw": _raw("MAX_TOKENS"), "parsed": None, "parsing_error": ValueError("truncated")}

    with pytest.raises(LLMTokenLimitError):
        unwrap_structured_result(result, ComponentGenerationOutput)


def test_unwrap_raises_invalid_output_with_parsing_error_as_cause() -> None:
    parsing_error = ValueError("broken json")
    result = {"raw": _raw("STOP"), "parsed": None, "parsing_error": parsing_error}

    with pytest.raises(LLMInvalidOutputError) as exc_info:
        unwrap_structured_result(result, ComponentGenerationOutput)

    assert exc_info.value.__cause__ is parsing_error


@pytest.mark.parametrize(
    ("exc", "reason_code"),
    [
        (LLMQuotaExceededError("quota"), "QUOTA_EXCEEDED"),
        (LLMTokenLimitError("tokens"), "TOKEN_LIMIT"),
        (LLMInvalidOutputError("invalid"), "INVALID_OUTPUT"),
        (RuntimeError("boom"), "GENERATION_FAILED"),
    ],
)
def test_classify_failure_maps_exceptions_to_reason_codes(exc: Exception, reason_code: str) -> None:
    failure = classify_failure(exc)

    assert failure.reason_code == reason_code
    assert "再度生成を指示してください" in failure.message


def test_classify_failure_looks_through_retry_wrapper_for_invalid_output() -> None:
    """invoke_with_retryはリトライ後にGenerationFailedErrorで包み直すため、原因を見て分類する。"""
    wrapped = GenerationFailedError("failed after retries")
    wrapped.__cause__ = LLMInvalidOutputError("invalid")

    assert classify_failure(wrapped).reason_code == "INVALID_OUTPUT"
    assert classify_failure(GenerationFailedError("x")).reason_code == "GENERATION_FAILED"
