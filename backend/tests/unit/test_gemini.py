import pytest

from app.ai.llm.fake import E2eFakeLLM
from app.ai.llm.gemini import extract_text_content, get_gemini_llm


def test_extract_text_content_returns_plain_string_as_is() -> None:
    assert extract_text_content("こんにちは") == "こんにちは"


def test_extract_text_content_extracts_text_from_thought_signature_blocks() -> None:
    content = [
        {"type": "text", "text": "通常の"},
        {"type": "text", "text": "テキスト", "extras": {"signature": "sig"}},
    ]

    assert extract_text_content(content) == "通常のテキスト"


def test_extract_text_content_handles_missing_text_field_as_empty() -> None:
    content = [{"type": "text", "extras": {"signature": "sig"}}]

    assert extract_text_content(content) == ""


class TestGetGeminiLlmE2eFakeBranch:
    """`get_gemini_llm`のE2E_FAKE_LLM分岐(app/ai/llm/fake.py)を検証する。
    実クライアント分岐(ChatGoogleGenerativeAI構築)はネットワーク呼び出しを伴わない
    コンストラクタ呼び出しのみのため、モックせずそのまま検証する。"""

    def test_returns_e2e_fake_llm_when_flag_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ai.llm import gemini as gemini_module

        monkeypatch.setattr(gemini_module.settings, "E2E_FAKE_LLM", True)
        get_gemini_llm.cache_clear()  # lru_cacheが前のテストの戻り値を持ち越さないようにする

        llm = get_gemini_llm()

        assert isinstance(llm, E2eFakeLLM)
        get_gemini_llm.cache_clear()  # 後続テストへ影響を残さない

    def test_real_client_receives_configured_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ai.llm import gemini as gemini_module

        monkeypatch.setattr(gemini_module.settings, "E2E_FAKE_LLM", False)
        monkeypatch.setattr(gemini_module.settings, "LLM_TIMEOUT_SECONDS", 12.5)
        get_gemini_llm.cache_clear()

        llm = get_gemini_llm()

        # timeoutはChatGoogleGenerativeAI(pydantic BaseModel)のフィールドとして保持される。
        assert llm.timeout == 12.5
        get_gemini_llm.cache_clear()
