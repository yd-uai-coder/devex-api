import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

import structlog
from langchain_core.messages import BaseMessage

from app.core.errors import AppError
from app.services.errors import GenerationFailedError, LLMQuotaExceededError, LLMTokenLimitError

T = TypeVar("T")

logger = structlog.get_logger(__name__)

# LLM呼び出しの一時的な失敗に対する最大リトライ回数・リトライ間隔(秒)。
# 汎用チャット(app/services/chat.py)も含め、全てのLLM呼び出しがこの値を使う。
MAX_GENERATION_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0

def prompt_char_count(messages: Sequence[BaseMessage]) -> int:
    """メッセージ列のうち文字列content部分だけの合計文字数を返す(DEBUGログ用)。
    本文自体はログに含めない(内部設計書3.4節「プライバシー上の注意」)。thought signature付き
    応答等、content が辞書のリストになる要素は対象外(文字列のcontentのみを数える)。"""
    return sum(len(m.content) for m in messages if isinstance(m.content, str))


async def invoke_with_retry[T](
    call: Callable[[], Awaitable[T]],
    *,
    messages: Sequence[BaseMessage] | None = None,
) -> T:
    """LLM呼び出しをラップし、クォータ超過は即座に諦め、それ以外の一時的エラーは規定回数までリトライする。

    chat_service.py(完了判定)・doc_generator_service.py(4文書生成+自己診断)の双方が同じ
    リトライ・クォータ判定ロジックを必要とするため、この共有モジュールに集約した
    (docs/implementation_plan.md 4.4節リスク1「指数バックオフ」の実装箇所)。

    ストリーミング応答(chat_service.ChatService.stream_reply)には適用していない ──
    ストリームは途中までクライアントへ送信済みの可能性があり、最初からやり直すのは安全でないため
    (この場合はストリームの失敗をそのまま伝播させ、クライアント側の再送に委ねる)。

    呼び出し成功時はDEBUGでレイテンシ・プロンプト文字数を、一時的失敗・クォータ超過は
    WARNINGを、規定回数リトライしても失敗した場合はERROR(スタックトレース付き)を記録する。
    全てのLLM呼び出し箇所がinvoke_with_retryを経由する設計
    (doc_generator_service.pyのdocstring参照)のため、ログ集約もここ一箇所に閉じられる。
    """
    prompt_chars = prompt_char_count(messages) if messages is not None else None
    last_error: Exception | None = None
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            result = await call()
            logger.debug(
                "llm_call_succeeded",
                attempt=attempt,
                prompt_chars=prompt_chars,
                latency_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return result
        except LLMTokenLimitError:
            # トークン上限超過は同じ入力で再試行しても解消しないため即座に諦める
            logger.warning("llm_token_limit_exceeded", attempt=attempt)
            raise
        except Exception as exc:
            if _is_input_token_limit_error(exc):
                logger.warning("llm_token_limit_exceeded", attempt=attempt)
                raise LLMTokenLimitError(
                    "AIへの入力が大きすぎるため処理できませんでした。"
                ) from exc
            if _is_quota_error(exc):
                # クォータ超過はリトライしても解消しないため即座に諦める
                logger.warning("llm_quota_exceeded", attempt=attempt)
                raise LLMQuotaExceededError(
                    "本日の利用上限に達しました。時間をおいて再度お試しください。"
                ) from exc
            last_error = exc
            logger.warning(
                "llm_call_failed",
                attempt=attempt,
                max_attempts=MAX_GENERATION_ATTEMPTS,
                error_type=type(exc).__name__,
            )
            if attempt < MAX_GENERATION_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY_SECONDS)
    logger.error(
        "llm_generation_failed_after_retries",
        max_attempts=MAX_GENERATION_ATTEMPTS,
        error_type=type(last_error).__name__ if last_error else None,
        exc_info=last_error,
    )
    raise GenerationFailedError(
        "AIからの応答生成に失敗しました。時間をおいて再度お試しください。"
    ) from last_error


def as_llm_error(exc: Exception) -> Exception:
    """再試行しないLLM呼び出し(ストリーミング)の失敗を、invoke_with_retryと同じ共通の例外に
    揃える(トークン上限 / クォータ超過 / それ以外の失敗)。既にAppErrorならそのまま返す。"""
    if isinstance(exc, AppError):
        return exc
    if _is_input_token_limit_error(exc):
        return LLMTokenLimitError("AIへの入力が大きすぎるため処理できませんでした。")
    if _is_quota_error(exc):
        return LLMQuotaExceededError("本日の利用上限に達しました。時間をおいて再度お試しください。")
    return GenerationFailedError("AIからの応答生成に失敗しました。時間をおいて再度お試しください。")


def _is_quota_error(exc: Exception) -> bool:
    """例外が外部APIのクォータ超過を示すものかどうかを判定する。Gemini APIの429と、Tavily(検索)の
    利用上限超過の2つ(Tavilyは汎用チャットのワークフローが使う)。"""
    try:
        from google.genai.errors import APIError as GoogleAPIError
    except ImportError:
        # google-genaiが未インストールの環境向けフォールバック
        GoogleAPIError = None
    try:
        from tavily import UsageLimitExceededError as TavilyUsageLimitExceededError
    except ImportError:
        # tavily-pythonが未インストールの環境向けフォールバック
        TavilyUsageLimitExceededError = None

    if (
        GoogleAPIError is not None
        and isinstance(exc, GoogleAPIError)
        and getattr(exc, "code", None) == 429
    ):
        return True
    return TavilyUsageLimitExceededError is not None and isinstance(
        exc, TavilyUsageLimitExceededError
    )


def _is_input_token_limit_error(exc: Exception) -> bool:
    """例外がGemini APIの「入力トークン数が上限を超えた」(400相当)を示すものかどうかを判定する。
    Gemini APIはこの場合に専用のエラーコードを持たず、400とメッセージで伝えるため、
    メッセージに"token"を含む400をトークン上限超過とみなす。"""
    try:
        from google.genai.errors import APIError as GoogleAPIError
    except ImportError:
        return False
    return (
        isinstance(exc, GoogleAPIError)
        and getattr(exc, "code", None) == 400
        and "token" in str(exc).lower()
    )
