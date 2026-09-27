from langchain_core.messages import AIMessage
from tests.fixtures.fake_llm import FakeLLM

from app.services import intake_file_processor as processor


async def test_txt_is_decoded_directly_without_llm() -> None:
    text, error = await processor.extract_text(file_type="txt", data="こんにちは".encode())

    assert text == "こんにちは"
    assert error is None


async def test_md_is_decoded_directly_without_llm() -> None:
    text, error = await processor.extract_text(file_type="md", data=b"# title\nbody")

    assert text == "# title\nbody"
    assert error is None


async def test_txt_decode_failure_is_reported_as_error() -> None:
    # UTF-8として不正なバイト列
    text, error = await processor.extract_text(file_type="txt", data=b"\xff\xfe\x00\x01")

    assert text is None
    assert error is not None


async def test_extracted_text_is_truncated_to_max_chars() -> None:
    long_text = "あ" * (processor.MAX_EXTRACTED_TEXT_CHARS + 100)

    text, error = await processor.extract_text(file_type="txt", data=long_text.encode())

    assert text is not None
    assert len(text) == processor.MAX_EXTRACTED_TEXT_CHARS
    assert error is None


async def test_pdf_uses_llm_native_file_understanding() -> None:
    fake_llm = FakeLLM(content="この文書はアーキテクチャ図を含む提案書です。")

    text, error = await processor.extract_text(file_type="pdf", data=b"%PDF-1.4 ...", llm=fake_llm)

    assert text == "この文書はアーキテクチャ図を含む提案書です。"
    assert error is None


async def test_pdf_extracts_text_from_thought_signature_content() -> None:
    """Geminiがthought signature付きの応答(contentが辞書のリスト)を返しても、
    text以外のメタデータ(extras/signature)を含めずプレーンテキストとして抽出されることを確認する。"""
    fake_llm = FakeLLM(
        content=[{"type": "text", "text": "抽出されたテキスト", "extras": {"signature": "sig"}}]
    )

    text, error = await processor.extract_text(file_type="pdf", data=b"%PDF-1.4 ...", llm=fake_llm)

    assert text == "抽出されたテキスト"
    assert error is None


async def test_pdf_llm_failure_is_reported_as_error() -> None:
    class _RaisingLLM:
        async def ainvoke(self, _messages: list, /) -> AIMessage:
            raise RuntimeError("quota exceeded")

    text, error = await processor.extract_text(
        file_type="pdf", data=b"%PDF-1.4 ...", llm=_RaisingLLM()
    )

    assert text is None
    assert error == "quota exceeded"


async def test_unsupported_file_type_is_reported_as_error() -> None:
    text, error = await processor.extract_text(file_type="docx", data=b"...")

    assert text is None
    assert error is not None
