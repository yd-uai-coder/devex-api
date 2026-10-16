"""登録する API キーが使えるかを、費用のかからない呼び出し(モデルの一覧の取得)で確かめる。

provider の SDK を使わず httpx で直接呼ぶ(一覧の1件だけを取れば足り、テストでは
`httpx.MockTransport` を差し込める)。キーは URL に載せず、ヘッダーで渡す(ログに残さないため)。
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum

import httpx

from app.ai.llm.catalog import Provider
from app.core.config import settings

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"

# provider がキーを無効と答える状態(Gemini は無効なキーに 400 API_KEY_INVALID を返す)
_INVALID_STATUSES = frozenset({400, 401, 403})


class KeyCheckResult(StrEnum):
    """確かめの結果。`UNREACHABLE` は通信の失敗・5xx・混雑で、キーの良し悪しを判断できない。"""

    VALID = "valid"
    INVALID = "invalid"
    UNREACHABLE = "unreachable"


type KeyVerifier = Callable[[Provider, str], Awaitable[KeyCheckResult]]


def _request_for(provider: Provider, api_key: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """provider ごとの (URL, クエリ, ヘッダー)。"""
    if provider is Provider.GEMINI:
        return GEMINI_MODELS_URL, {"pageSize": "1"}, {"x-goog-api-key": api_key}
    return (
        ANTHROPIC_MODELS_URL,
        {"limit": "1"},
        {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
    )


async def verify_api_key(
    provider: Provider, api_key: str, *, client: httpx.AsyncClient
) -> KeyCheckResult:
    """モデルの一覧を1件だけ取り、応答の状態でキーを判定する。"""
    url, params, headers = _request_for(provider, api_key)
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError:
        return KeyCheckResult.UNREACHABLE
    if response.status_code == 200:
        return KeyCheckResult.VALID
    if response.status_code in _INVALID_STATUSES:
        return KeyCheckResult.INVALID
    return KeyCheckResult.UNREACHABLE


async def _verify_with_new_client(provider: Provider, api_key: str) -> KeyCheckResult:
    async with httpx.AsyncClient(timeout=settings.API_KEY_CHECK_TIMEOUT_SECONDS) as client:
        return await verify_api_key(provider, api_key, client=client)


async def _accept_without_check(_provider: Provider, _api_key: str) -> KeyCheckResult:
    return KeyCheckResult.VALID


def get_key_verifier() -> KeyVerifier:
    """ルートが使う確かめの関数(FastAPI の依存。テストは差し替える)。

    `E2E_FAKE_LLM` のときは外部を呼ばずに有効とみなす(E2E を無料・決定論的に流すため)。
    """
    if settings.E2E_FAKE_LLM:
        return _accept_without_check
    return _verify_with_new_client
