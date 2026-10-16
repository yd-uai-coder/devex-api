"""応答の本文の取り出し(extract_text_content)のテスト。

SUT は app/ai/llm/content.py の extract_text_content。ドライバは pytest。スタブ不要 ── 対象が純粋
(副作用なし)で外部依存を呼ばないため。
"""

from app.ai.llm.content import extract_text_content


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


def test_extract_text_content_reads_claude_text_blocks() -> None:
    # Claude はツールの呼び出しなど本文以外のブロックを混ぜることがある(text だけを読む)
    content = [{"type": "text", "text": "回答"}, {"type": "tool_use", "id": "t1", "input": {}}]

    assert extract_text_content(content) == "回答"
