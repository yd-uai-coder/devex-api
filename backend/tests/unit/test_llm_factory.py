"""LLM のクライアントの組み立て(provider ごとの引数・キャッシュ・E2E の偽物)のテスト。

SUT: app/ai/llm/factory.py の build_llm・timeout_seconds・max_output_tokens
ドライバ: pytest のテスト関数
スタブ不要 ── 2つのクライアントのコンストラクタはネットワークを呼ばないので、そのまま作って
フィールドを確かめる。E2E の分岐は設定値を monkeypatch で差し替えるだけ。
"""

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from app.ai.llm.catalog import Provider, Purpose
from app.ai.llm.factory import (
    DEFAULT_GEMINI_TEMPERATURE,
    STRUCTURED_METHOD,
    build_llm,
    max_output_tokens,
    timeout_seconds,
)
from app.ai.llm.fake import E2eFakeLLM
from app.core.config import settings


@pytest.fixture(autouse=True)
def _real_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", False)


def test_gemini_client_uses_catalog_timeout_and_one_sdk_retry() -> None:
    llm = build_llm(Provider.GEMINI, "gemini-3.8-flash", "g-key", Purpose.GENERATION)

    assert isinstance(llm, ChatGoogleGenerativeAI)
    assert llm.model.endswith("gemini-3.8-flash")
    assert llm.timeout == 240.0
    # 再試行は invoke_with_retry だけにする(SDK の中は 1 = 止める。0 は Google の既定の意味)
    assert llm.max_retries == 1
    assert llm.temperature == DEFAULT_GEMINI_TEMPERATURE
    # チャット・生成はモデルの上限まで書かせる
    assert llm.max_output_tokens is None


def test_gemini_client_caps_output_for_judge_and_takes_temperature() -> None:
    llm = build_llm(Provider.GEMINI, "gemini-3.5-flash-lite", "g-key", Purpose.JUDGE, 0.0)

    assert llm.max_output_tokens == settings.JUDGE_MAX_TOKENS
    assert llm.temperature == 0.0
    assert llm.timeout == 30.0


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        (Purpose.CHAT, 4000),
        (Purpose.GENERATION, 16000),
        (Purpose.REVIEW, None),
        (Purpose.JUDGE, None),
    ],
)
def test_claude_client_always_has_output_limit(purpose: Purpose, expected: int | None) -> None:
    llm = build_llm(Provider.CLAUDE, "claude-haiku-5-5", "c-key", purpose)

    assert isinstance(llm, ChatAnthropic)
    assert llm.model == "claude-haiku-5-5"
    assert llm.max_tokens == (expected or max_output_tokens(purpose))
    assert llm.default_request_timeout == 60.0
    assert llm.max_retries == 0
    assert llm.anthropic_api_key.get_secret_value() == "c-key"


def test_same_arguments_reuse_the_client_and_keys_separate_them() -> None:
    first = build_llm(Provider.CLAUDE, "claude-sonnet-5-5", "key-a", Purpose.REVIEW)

    assert build_llm(Provider.CLAUDE, "claude-sonnet-5-5", "key-a", Purpose.REVIEW) is first
    assert build_llm(Provider.CLAUDE, "claude-sonnet-5-5", "key-b", Purpose.REVIEW) is not first
    assert build_llm(Provider.CLAUDE, "claude-sonnet-5-5", "key-a", Purpose.JUDGE) is not first


def test_e2e_flag_returns_fake_even_after_real_client_was_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_llm(Provider.GEMINI, "gemini-3.5-flash-lite", "g-key", Purpose.CHAT)
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", True)

    assert isinstance(
        build_llm(Provider.GEMINI, "gemini-3.5-flash-lite", "g-key", Purpose.CHAT), E2eFakeLLM
    )


def test_model_outside_catalog_is_rejected() -> None:
    with pytest.raises(ValueError):
        timeout_seconds(Provider.GEMINI, "claude-haiku-5-5")
    with pytest.raises(ValueError):
        build_llm(Provider.CLAUDE, "claude-unknown", "c-key", Purpose.CHAT)


def test_structured_method_is_json_schema_for_both_providers() -> None:
    # langchain-anthropic の既定(function_calling)に頼らず、両 provider で同じ方式を明示する
    assert STRUCTURED_METHOD == "json_schema"
