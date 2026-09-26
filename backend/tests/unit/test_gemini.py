from app.ai.llm.gemini import extract_text_content


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
