import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.services.errors import GenerationFailedError, LLMQuotaExceededError

T = TypeVar("T")

# LLM呼び出しの一時的な失敗に対する最大リトライ回数・リトライ間隔(秒)。
# app/services/chat.py(既存の汎用デモ)の_invoke_with_retryと同じ値を踏襲する。
MAX_GENERATION_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0


async def invoke_with_retry(call: Callable[[], Awaitable[T]]) -> T:
    """LLM呼び出しをラップし、クォータ超過は即座に諦め、それ以外の一時的エラーは規定回数までリトライする。

    chat_service.py(完了判定)・doc_generator_service.py(4文書生成+自己診断)の双方が同じ
    リトライ・クォータ判定ロジックを必要とするため、この共有モジュールに集約した
    (docs/implementation_plan.md 4.4節リスク1「指数バックオフ」の実装箇所)。

    ストリーミング応答(chat_service.ChatService.stream_reply)には適用していない ──
    ストリームは途中までクライアントへ送信済みの可能性があり、最初からやり直すのは安全でないため
    (この場合はストリームの失敗をそのまま伝播させ、クライアント側の再送に委ねる)。
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        try:
            return await call()
        except Exception as exc:
            if _is_quota_error(exc):
                # クォータ超過はリトライしても解消しないため即座に諦める
                raise LLMQuotaExceededError(
                    "本日の利用上限に達しました。時間をおいて再度お試しください。"
                ) from exc
            last_error = exc
            if attempt < MAX_GENERATION_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
    raise GenerationFailedError(
        "AIからの応答生成に失敗しました。時間をおいて再度お試しください。"
    ) from last_error


def _is_quota_error(exc: Exception) -> bool:
    """例外がGemini APIのクォータ超過(429相当)を示すものかどうかを判定する。
    app/services/chat.pyの同名関数と同じ判定基準(Tavily分はDevexでは使わないため除く)。"""
    try:
        from google.genai.errors import APIError as GoogleAPIError
    except ImportError:
        # google-genaiが未インストールの環境向けフォールバック
        return False
    return isinstance(exc, GoogleAPIError) and getattr(exc, "code", None) == 429
