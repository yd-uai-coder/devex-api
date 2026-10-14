from functools import lru_cache
from typing import Any

from langchain_anthropic import ChatAnthropic
from pydantic import SecretStr

from app.ai.llm.fake import get_e2e_fake_llm
from app.core.config import settings
from app.services.errors import ReviewUnavailableError


@lru_cache
def get_review_llm() -> Any:
    """設計レビューの LLM(Claude Sonnet。モデル名は`settings.REVIEW_MODEL`)を返す。

    生成(`get_gemini_llm`)とは別のモデルにする。同じモデルの自己診断では、文書をまたぐ矛盾や
    認可の抜けがほとんど当たらなかったため(docs/external_design.md 2.11節)。
    `settings.E2E_FAKE_LLM`が有効なら、生成と同じ決定論的な`E2eFakeLLM`を返す。
    キーが無いときは`ReviewUnavailableError`(呼び出し側は失敗を記録して自己診断に切り替える)。

    タイムアウトは生成と分けた`REVIEW_TIMEOUT_SECONDS`。SDK 自身の再試行は切り、再試行は
    invoke_with_retry に任せる(二重に重ねると、止まった回の回収のしきい値を超えうるため)。
    """
    if settings.E2E_FAKE_LLM:
        return get_e2e_fake_llm()
    if not settings.ANTHROPIC_API_KEY:
        raise ReviewUnavailableError("設計レビューのAPIキーが設定されていません。")
    return ChatAnthropic(
        model_name=settings.REVIEW_MODEL,
        api_key=SecretStr(settings.ANTHROPIC_API_KEY),
        max_tokens_to_sample=settings.REVIEW_MAX_TOKENS,
        timeout=settings.REVIEW_TIMEOUT_SECONDS,
        max_retries=0,
        stop=None,
    )


@lru_cache
def get_judge_llm() -> Any:
    """狭い判定(採用した指摘は直ったか)の LLM(Claude Haiku。モデル名は`settings.JUDGE_MODEL`)。

    対象と問いが決まった判定なので、レビュー(Sonnet)より安いモデルに任せる
    (docs/external_design.md 2.11節)。E2E・キーが無いときの扱いと、SDK の再試行を切る理由は
    `get_review_llm`と同じ。
    """
    if settings.E2E_FAKE_LLM:
        return get_e2e_fake_llm()
    if not settings.ANTHROPIC_API_KEY:
        raise ReviewUnavailableError("設計レビューのAPIキーが設定されていません。")
    return ChatAnthropic(
        model_name=settings.JUDGE_MODEL,
        api_key=SecretStr(settings.ANTHROPIC_API_KEY),
        max_tokens_to_sample=settings.JUDGE_MAX_TOKENS,
        timeout=settings.JUDGE_TIMEOUT_SECONDS,
        max_retries=0,
        stop=None,
    )
