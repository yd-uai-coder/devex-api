import asyncio

import pytest
import structlog.testing
from langchain_core.messages import HumanMessage

from app.services import llm_retry
from app.services.errors import GenerationFailedError, LLMQuotaExceededError, LLMTokenLimitError
from app.services.llm_retry import invoke_with_retry, prompt_char_count


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """リトライ間隔(1秒)をテストで待たないようにする。"""

    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_retry.asyncio, "sleep", _instant_sleep)


async def test_returns_result_on_first_success() -> None:
    async def _call() -> str:
        return "ok"

    result = await invoke_with_retry(_call)

    assert result == "ok"


async def test_retries_transient_failure_and_eventually_succeeds() -> None:
    calls = {"n": 0}

    async def _call() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient network error")
        return "recovered"

    result = await invoke_with_retry(_call)

    assert result == "recovered"
    assert calls["n"] == 3


async def test_raises_generation_failed_after_exhausting_attempts() -> None:
    calls = {"n": 0}

    async def _call() -> str:
        calls["n"] += 1
        raise RuntimeError("always fails")

    with pytest.raises(GenerationFailedError) as exc_info:
        await invoke_with_retry(_call)

    assert calls["n"] == llm_retry.MAX_GENERATION_ATTEMPTS
    assert "時間をおいて再度お試しください" in str(exc_info.value)


async def test_fails_fast_on_quota_error_without_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    # _is_quota_errorの判定基準(google.genai.errors.APIErrorの構築)自体はapp/services/chat.pyの
    # 既存実装と同一のため、ここではその判定結果(True)に対するinvoke_with_retryの
    # 振る舞いを検証する。
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)
    calls = {"n": 0}

    async def _call() -> str:
        calls["n"] += 1
        raise RuntimeError("quota exceeded")

    with pytest.raises(LLMQuotaExceededError):
        await invoke_with_retry(_call)

    assert calls["n"] == 1  # リトライせず1回で諦める

async def test_treats_timeout_error_as_transient_retryable_failure() -> None:
    """`asyncio.TimeoutError`(get_gemini_llm()のtimeout設定超過時にlangchain-google-genaiが
    送出しうる例外)は、_is_quota_errorに該当しないため他の一時的失敗と同じ扱いになり、
    リトライを経て最終的にGenerationFailedErrorになることを確認する(docs/implementation_plan.md
    4.2節「LLM呼び出しタイムアウト時の挙動確認」に対応するテスト)。"""
    calls = {"n": 0}

    async def _call() -> str:
        calls["n"] += 1
        raise TimeoutError("Gemini呼び出しがタイムアウトしました")

    with pytest.raises(GenerationFailedError):
        await invoke_with_retry(_call)

    assert calls["n"] == llm_retry.MAX_GENERATION_ATTEMPTS


async def test_asyncio_timeout_error_is_treated_the_same_as_builtin_timeout_error() -> None:
    """Python 3.11以降`asyncio.TimeoutError`は`TimeoutError`のエイリアスであることを
    明示的に固定する(将来のPythonバージョンでこの関係が変わった場合に検知するための回帰テスト)。"""
    assert asyncio.TimeoutError is TimeoutError


def test_prompt_char_count_sums_only_string_contents() -> None:
    """thought signature付き応答等、contentが辞書のリストになる要素は数えない
    (対象は文字列contentのみ、本文自体はログに含めないため文字数だけを算出する)。"""
    messages = [
        HumanMessage(content="12345"),
        HumanMessage(content=[{"type": "text", "text": "ignored"}]),
    ]

    assert prompt_char_count(messages) == 5


async def test_logs_debug_with_latency_and_prompt_chars_on_success() -> None:
    """成功時、本文を含まないDEBUGログ(レイテンシ・プロンプト文字数)を記録する。"""

    async def _call() -> str:
        return "ok"

    with structlog.testing.capture_logs() as logs:
        await invoke_with_retry(_call, messages=[HumanMessage(content="12345")])

    debug_logs = [log for log in logs if log["log_level"] == "debug"]
    assert len(debug_logs) == 1
    assert debug_logs[0]["event"] == "llm_call_succeeded"
    assert debug_logs[0]["prompt_chars"] == 5
    assert "latency_ms" in debug_logs[0]


async def test_logs_warning_on_quota_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)

    async def _call() -> str:
        raise RuntimeError("quota exceeded")

    with structlog.testing.capture_logs() as logs, pytest.raises(LLMQuotaExceededError):
        await invoke_with_retry(_call)

    assert any(
        log["log_level"] == "warning" and log["event"] == "llm_quota_exceeded" for log in logs
    )


async def test_logs_error_after_exhausting_retries() -> None:
    async def _call() -> str:
        raise RuntimeError("always fails")

    with structlog.testing.capture_logs() as logs, pytest.raises(GenerationFailedError):
        await invoke_with_retry(_call)

    error_logs = [log for log in logs if log["log_level"] == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["event"] == "llm_generation_failed_after_retries"


async def test_does_not_retry_token_limit_error() -> None:
    """Phase 10: トークン上限超過は同じ入力で再試行しても変わらないため、1回で諦める。"""
    calls = {"n": 0}

    async def _call() -> str:
        calls["n"] += 1
        raise LLMTokenLimitError("出力がMAX_TOKENSで打ち切られた")

    with pytest.raises(LLMTokenLimitError):
        await invoke_with_retry(_call)

    assert calls["n"] == 1


async def test_converts_input_token_limit_api_error_without_retrying() -> None:
    """Phase 10: 入力トークン数の超過(Gemini APIの400)はLLMTokenLimitErrorにして1回で諦める。"""
    from google.genai.errors import APIError

    calls = {"n": 0}

    async def _call() -> str:
        calls["n"] += 1
        raise APIError(
            400, {"error": {"message": "The input token count exceeds the maximum"}}
        )

    with pytest.raises(LLMTokenLimitError):
        await invoke_with_retry(_call)

    assert calls["n"] == 1
