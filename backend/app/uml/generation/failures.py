"""構造化出力の結果の解釈と、生成失敗の理由の分類(純粋関数)。

生成が止まった理由は、生成履歴(uml_generation_runs)と図の`generation_error`としてユーザーに
見せる。伝える内容は「止まった理由」と「再度の生成指示が必要なこと」の2点に揃える。
"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from app.services.errors import (
    GenerationFailedError,
    LLMInvalidOutputError,
    LLMQuotaExceededError,
    LLMTokenLimitError,
)

ReasonCode = Literal[
    "QUOTA_EXCEEDED", "TOKEN_LIMIT", "INVALID_OUTPUT", "GENERATION_FAILED", "STALE_GENERATION"
]

_MESSAGES: dict[ReasonCode, str] = {
    "QUOTA_EXCEEDED": (
        "AIの利用上限(無料枠のクォータ)に達したため、生成を中断しました。"
        "時間をおいて、再度生成を指示してください。"
    ),
    "TOKEN_LIMIT": (
        "AIの入力または出力のトークン数が上限を超えたため、生成できませんでした。"
        "ER図はテーブルを絞った部分図にするなど対象を小さくして、再度生成を指示してください。"
    ),
    "INVALID_OUTPUT": "AIの出力を設計図として解釈できませんでした。再度生成を指示してください。",
    "GENERATION_FAILED": "設計図の生成に失敗しました。時間をおいて、再度生成を指示してください。",
    # 例外の分類では出てこない(生成中のまま止まったものの回収用。詳細設計モードは段階の生成の
    # サービスが自分の文言を持つ)
    "STALE_GENERATION": (
        "生成が時間内に終わらなかったため、中断しました。再度生成を指示してください。"
    ),
}

@dataclass(frozen=True)
class GenerationFailure:
    """生成失敗1件の理由コードとユーザー向けの文言。"""

    reason_code: ReasonCode
    message: str


def classify_failure(exc: BaseException) -> GenerationFailure:
    """生成中に送出された例外を、理由コードとユーザー向けの文言に分類する。
    invoke_with_retryは規定回数のリトライ後に`GenerationFailedError`で包み直すため、
    最後の失敗の原因(`__cause__`)も見て「出力を解釈できなかった」を見分ける。"""
    reason: ReasonCode
    if isinstance(exc, LLMQuotaExceededError):
        reason = "QUOTA_EXCEEDED"
    elif isinstance(exc, LLMTokenLimitError):
        reason = "TOKEN_LIMIT"
    elif isinstance(exc, LLMInvalidOutputError) or (
        isinstance(exc, GenerationFailedError) and isinstance(exc.__cause__, LLMInvalidOutputError)
    ):
        reason = "INVALID_OUTPUT"
    else:
        reason = "GENERATION_FAILED"
    return GenerationFailure(reason_code=reason, message=_MESSAGES[reason])


def unwrap_structured_result[T: BaseModel](result: dict[str, Any], schema: type[T]) -> T:
    """`with_structured_output(schema, include_raw=True)`の戻り値
    ({"raw": AIMessage, "parsed": T | None, "parsing_error": Exception | None})を解釈する。

    `include_raw=True`にするのは、解釈に失敗したときに「出力が`MAX_TOKENS`で打ち切られた」
    (トークン上限。再試行しても同じ)のか、「出力が揺らいで壊れた」(再試行で直りうる)のかを、
    生の応答の`finish_reason`で見分けるため。
    """
    parsed = result.get("parsed")
    if isinstance(parsed, schema):
        return parsed
    raw = result.get("raw")
    metadata = getattr(raw, "response_metadata", None) or {}
    if metadata.get("finish_reason") == "MAX_TOKENS":
        raise LLMTokenLimitError("AIの出力がトークン数の上限で打ち切られました。")
    raise LLMInvalidOutputError("AIの出力を出力スキーマとして解釈できませんでした。") from (
        result.get("parsing_error")
    )
