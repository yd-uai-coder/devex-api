"""API キーの確かめ(モデルの一覧の取得)のテスト。

SUT: app/ai/llm/key_check.py の verify_api_key・get_key_verifier
ドライバ: pytest のテスト関数
スタブ: httpx.MockTransport(provider の応答を差し替える。実際の Gemini・Anthropic は呼ばない)
"""

import httpx
import pytest

from app.ai.llm import key_check
from app.ai.llm.catalog import Provider
from app.ai.llm.key_check import KeyCheckResult, get_key_verifier, verify_api_key
from app.core.config import settings


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_gemini_sends_key_in_header_not_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"models": []})

    async with _client(httpx.MockTransport(handler)) as client:
        result = await verify_api_key(Provider.GEMINI, "AIza-test-key", client=client)

    assert result is KeyCheckResult.VALID
    assert str(seen[0].url).startswith(key_check.GEMINI_MODELS_URL)
    assert "AIza-test-key" not in str(seen[0].url)
    assert seen[0].headers["x-goog-api-key"] == "AIza-test-key"


async def test_claude_sends_key_and_version_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    async with _client(httpx.MockTransport(handler)) as client:
        result = await verify_api_key(Provider.CLAUDE, "sk-ant-test", client=client)

    assert result is KeyCheckResult.VALID
    assert str(seen[0].url).startswith(key_check.ANTHROPIC_MODELS_URL)
    assert seen[0].headers["x-api-key"] == "sk-ant-test"
    assert seen[0].headers["anthropic-version"] == key_check.ANTHROPIC_VERSION


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, KeyCheckResult.INVALID),
        (401, KeyCheckResult.INVALID),
        (403, KeyCheckResult.INVALID),
        (429, KeyCheckResult.UNREACHABLE),
        (500, KeyCheckResult.UNREACHABLE),
        (503, KeyCheckResult.UNREACHABLE),
    ],
)
async def test_status_classification(status: int, expected: KeyCheckResult) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(status))
    async with _client(transport) as client:
        assert await verify_api_key(Provider.CLAUDE, "sk-ant-x", client=client) is expected


async def test_connection_error_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    async with _client(httpx.MockTransport(handler)) as client:
        result = await verify_api_key(Provider.GEMINI, "AIza-x", client=client)

    assert result is KeyCheckResult.UNREACHABLE


async def test_e2e_fake_accepts_without_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", True)

    verifier = get_key_verifier()

    assert await verifier(Provider.CLAUDE, "anything") is KeyCheckResult.VALID
