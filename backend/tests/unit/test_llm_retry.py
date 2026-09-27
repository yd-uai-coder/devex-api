import pytest

from app.services import llm_retry
from app.services.errors import GenerationFailedError, LLMQuotaExceededError
from app.services.llm_retry import invoke_with_retry


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
