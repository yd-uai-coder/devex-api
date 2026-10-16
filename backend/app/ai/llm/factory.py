"""provider・モデル・キー・用途から LLM のクライアントを組み立てる(どのユーザーのどの用途かを
決めるのは app/services/llm_resolver.py)。

provider に固有の引数(出力の上限・SDK の中の再試行)はここに閉じる。呼び出し側は provider を
見ずに、構造化出力を `STRUCTURED_METHOD` で呼べばよい。
"""

from functools import lru_cache
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr

from app.ai.llm.catalog import Provider, Purpose, find_model
from app.ai.llm.fake import get_e2e_fake_llm
from app.core.config import settings

# 構造化出力の方式。langchain-anthropic の既定は function_calling(ツールの呼び出し)で、
# langchain-google-genai の既定は json_schema。両 provider で同じ方式に揃える
STRUCTURED_METHOD = "json_schema"

# Gemini の温度の既定(チャットの応答・生成)
DEFAULT_GEMINI_TEMPERATURE = 0.7

# Claude の出力の上限(Anthropic の API は必須)。チャットの返信は短く、生成は1文書・1段階を
# まとめて書くので大きく取る。レビュー・判定は設定値
_CLAUDE_CHAT_MAX_TOKENS = 4000
_CLAUDE_GENERATION_MAX_TOKENS = 16000


def max_output_tokens(purpose: Purpose) -> int | None:
    """用途の出力の上限。Gemini はレビュー・判定だけに付け(チャット・生成はモデルの上限まで)、
    Claude はこの値か、None のときはチャット・生成の既定を使う。"""
    if purpose is Purpose.REVIEW:
        return settings.REVIEW_MAX_TOKENS
    if purpose is Purpose.JUDGE:
        return settings.JUDGE_MAX_TOKENS
    return None


def timeout_seconds(provider: Provider, model: str) -> float:
    """モデルの1回の呼び出しを待つ上限(カタログの値)。カタログに無いモデルは作らない。"""
    entry = find_model(provider, model)
    if entry is None:
        raise ValueError(f"選べるモデルの表に無いモデルです: {provider}/{model}")
    return entry.timeout_seconds


def build_llm(
    provider: Provider,
    model: str,
    api_key: str | None,
    purpose: Purpose,
    temperature: float | None = None,
) -> Any:
    """LLM のクライアントを返す。同じ (provider, モデル, キー, 用途, 温度) なら同じものを使い回す
    (クライアントは接続を持つので、呼び出しのたびに作らない)。

    `settings.E2E_FAKE_LLM` が有効なら、実際の API を呼ばない決定論的な `E2eFakeLLM` を返す
    (ブラウザE2Eを無料・決定論的に流すため。本番では起動時にこのフラグ自体が拒否される)。
    戻り値が Any なのは、E2eFakeLLM が LangChain の Runnable を継承しない軽量スタブで、2つの
    クライアントと共通の基底型が無いため。

    `timeout` を必ず付ける(付けないと応答がハングしたときに無制限に待つ。invoke_with_retry の
    再試行は例外のときだけ働く)。SDK の中の再試行は止め、再試行は invoke_with_retry に任せる
    (二重に重ねると1回の呼び出しがタイムアウトを大きく超える)。Gemini の `max_retries=0` は
    「Google の既定(5回)」の意味になるので 1 にする。
    """
    if settings.E2E_FAKE_LLM:
        return get_e2e_fake_llm()
    return _build_client(provider, model, api_key, purpose, temperature)


@lru_cache(maxsize=64)
def _build_client(
    provider: Provider,
    model: str,
    api_key: str | None,
    purpose: Purpose,
    temperature: float | None,
) -> Any:
    """実クライアントを作る(引数の組ごとに1つ。E2E の分岐はキャッシュの外に置く)。"""
    timeout = timeout_seconds(provider, model)
    limit = max_output_tokens(purpose)
    if provider is Provider.GEMINI:
        options: dict[str, Any] = {} if limit is None else {"max_output_tokens": limit}
        return ChatGoogleGenerativeAI(
            model=model,
            api_key=api_key,
            temperature=DEFAULT_GEMINI_TEMPERATURE if temperature is None else temperature,
            timeout=timeout,
            max_retries=1,
            **options,
        )
    if limit is None:
        chat = purpose is Purpose.CHAT
        limit = _CLAUDE_CHAT_MAX_TOKENS if chat else _CLAUDE_GENERATION_MAX_TOKENS
    claude_options: dict[str, Any] = {} if temperature is None else {"temperature": temperature}
    return ChatAnthropic(
        model_name=model,
        api_key=SecretStr(api_key or ""),
        max_tokens_to_sample=limit,
        timeout=timeout,
        max_retries=0,
        stop=None,
        **claude_options,
    )
