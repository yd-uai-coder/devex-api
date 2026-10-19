import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

import structlog
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.ai.llm.factory import STRUCTURED_METHOD
from app.services.errors import GenerationFailedError, LLMQuotaExceededError, LLMTokenLimitError
from app.uml.generation.failures import unwrap_structured_result

T = TypeVar("T")

logger = structlog.get_logger(__name__)

# LLM呼び出しの一時的な失敗に対する最大リトライ回数・リトライ間隔(秒)。
# 全てのLLM呼び出しがこの値を使う。
MAX_GENERATION_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0
# 混雑(Gemini の 503・504、Anthropic の 529・503)のときの、試行の間の待ち(1回目の失敗の後、
# 2回目の失敗の後)。混雑は1秒では引かないことが多いので、待ちを伸ばす
OVERLOAD_RETRY_DELAYS = (5.0, 15.0)

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

    chat_service.py(観点の判定・返信)・doc_generator_service.py(4文書生成+自己診断)の双方が同じ
    リトライ・クォータ判定ロジックを必要とするため、この共有モジュールに集約した
    (docs/implementation_plan.md 4.4節リスク1「指数バックオフ」の実装箇所)。

    ヒアリングの返信も、本文と選択肢カードを構造化出力の1回で作るため(トークンごとには流さず、
    クライアントへ何も送っていない段階で呼ぶため)、ここでリトライしてよい。

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
                await asyncio.sleep(_retry_delay(exc, attempt))
    logger.error(
        "llm_generation_failed_after_retries",
        max_attempts=MAX_GENERATION_ATTEMPTS,
        error_type=type(last_error).__name__ if last_error else None,
        exc_info=last_error,
    )
    raise GenerationFailedError(
        "AIからの応答生成に失敗しました。時間をおいて再度お試しください。"
    ) from last_error


def _retry_delay(exc: Exception, attempt: int) -> float:
    """`attempt` 回目の失敗の後に待つ秒数(混雑は長く、ほかは短く)。"""
    if _is_overloaded_error(exc):
        return OVERLOAD_RETRY_DELAYS[min(attempt, len(OVERLOAD_RETRY_DELAYS)) - 1]
    return RETRY_DELAY_SECONDS


def _status_code(exc: Exception) -> int | None:
    """API の例外の HTTP の状態コード(Gemini は `code`、Anthropic は `status_code`)。"""
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _is_overloaded_error(exc: Exception) -> bool:
    """例外が provider の混雑を示すか(Gemini の 503・504、Anthropic の 529(Overloaded)・503)。"""
    try:
        from anthropic import APIStatusError as AnthropicStatusError
    except ImportError:
        AnthropicStatusError = None
    try:
        from google.genai.errors import APIError as GoogleAPIError
    except ImportError:
        GoogleAPIError = None
    if GoogleAPIError is not None and isinstance(exc, GoogleAPIError):
        return _status_code(exc) in (503, 504)
    if AnthropicStatusError is not None and isinstance(exc, AnthropicStatusError):
        return _status_code(exc) in (503, 529)
    return False


def _is_quota_error(exc: Exception) -> bool:
    """例外が外部APIのクォータ超過を示すものかどうかを判定する。Gemini APIの429と、
    Anthropic APIの429(設計レビュー)。"""
    try:
        from anthropic import RateLimitError as AnthropicRateLimitError
    except ImportError:
        AnthropicRateLimitError = None
    if AnthropicRateLimitError is not None and isinstance(exc, AnthropicRateLimitError):
        return True
    try:
        from google.genai.errors import APIError as GoogleAPIError
    except ImportError:
        # google-genaiが未インストールの環境向けフォールバック
        GoogleAPIError = None

    return (
        GoogleAPIError is not None
        and isinstance(exc, GoogleAPIError)
        and getattr(exc, "code", None) == 429
    )


def _is_input_token_limit_error(exc: Exception) -> bool:
    """例外が「入力トークン数が上限を超えた」を示すものかどうかを判定する。
    Gemini APIはこの場合に専用のエラーコードを持たず、400とメッセージで伝えるため、
    メッセージに"token"を含む400をトークン上限超過とみなす。Anthropic APIは 400 の
    「prompt is too long」か、要求そのものが大きすぎる 413 で伝える。"""
    try:
        from google.genai.errors import APIError as GoogleAPIError
    except ImportError:
        GoogleAPIError = None
    try:
        from anthropic import APIStatusError as AnthropicStatusError
    except ImportError:
        AnthropicStatusError = None
    message = str(exc).lower()
    if GoogleAPIError is not None and isinstance(exc, GoogleAPIError):
        return _status_code(exc) == 400 and "token" in message
    if AnthropicStatusError is not None and isinstance(exc, AnthropicStatusError):
        status = _status_code(exc)
        return status == 413 or (status == 400 and "prompt is too long" in message)
    return False


async def invoke_structured[S: BaseModel](
    llm, schema: type[S], messages: list[BaseMessage]
) -> S:
    """構造化出力を呼ぶ(解釈の失敗・クォータ超過は invoke_with_retry の規則で再試行する)。
    詳細設計モードの段階の下書きと、要件定義書の生成が使う。"""
    parsed, _usage = await invoke_structured_with_usage(llm, schema, messages)
    return parsed


async def invoke_structured_with_usage[S: BaseModel](
    llm, schema: type[S], messages: list[BaseMessage], *, method: str = STRUCTURED_METHOD
) -> tuple[S, dict[str, Any]]:
    """`invoke_structured`に、生の応答のトークン数(`usage_metadata`。無ければ空)を添えて返す。
    `method`は構造化出力の方式(既定は両 provider で揃えた`STRUCTURED_METHOD`)。
    設計レビューが、回ごとのトークン数を記録するために使う。"""
    structured_llm = llm.with_structured_output(schema, include_raw=True, method=method)

    async def _call() -> tuple[S, dict[str, Any]]:
        result = await structured_llm.ainvoke(messages)
        usage = getattr(result.get("raw"), "usage_metadata", None) or {}
        return unwrap_structured_result(result, schema), dict(usage)

    return await invoke_with_retry(_call, messages=messages)
