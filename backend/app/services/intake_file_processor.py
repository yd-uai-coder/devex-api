import base64
from typing import Protocol

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.llm.gemini import extract_text_content, get_gemini_llm

# 対応する添付ファイル形式(docs/external_design.md 2.5節5項)。Word/Excel/PowerPointは非対応。
ALLOWED_FILE_TYPES = ("txt", "md", "pdf")

# 1ファイルあたりの抽出テキスト上限文字数。超過分は切り詰める。
MAX_EXTRACTED_TEXT_CHARS = 20_000

_PDF_EXTRACTION_PROMPT = (
    "以下のPDF文書の内容をできるだけ忠実にテキスト化してください。"
    "図表・グラフ・図解が含まれる場合は、その内容も文章で説明に含めてください。"
    "前置きや要約は不要です。文書の内容そのものを出力してください。"
)


class _StructuredContentLLM(Protocol):
    """このモジュールが要求するLLMクライアントの最小インターフェース(FakeLLMでのスタブ差し替え用)。

    messagesを位置専用引数(`/`)にしているのは、実装側(ChatGoogleGenerativeAI.ainvoke)の
    第1引数名が`input`でありFakeLLMの`messages`と異なるため、引数名の違いを型チェック上
    無視できるようにするため(位置専用パラメータは名前の一致を要求しない)。"""

    async def ainvoke(self, messages: list, /) -> AIMessage: ...


async def extract_text(
    *, file_type: str, data: bytes, llm: _StructuredContentLLM | None = None
) -> tuple[str | None, str | None]:
    """添付ファイルのバイト列からテキストを抽出する。

    戻り値は (extracted_text, error_message) のタプル。抽出に成功した場合は
    extracted_text に切り詰め済みのテキストが入り error_message は None、失敗した場合は
    extracted_text が None で error_message に理由が入る(呼び出し側はこれをそのまま
    IntakeFile.status='failed' として記録する。ヒアリング自体はブロックしない)。

    txt/mdはUTF-8テキストとしてそのまま読み込む(LLM呼び出し不要)。pdfはGeminiのネイティブな
    ファイル理解でテキスト化する(図・レイアウトの解釈を含む)。
    """
    if file_type in ("txt", "md"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None, "テキストのデコードに失敗しました(UTF-8以外の可能性があります)"
    elif file_type == "pdf":
        try:
            text = await _extract_pdf_text(data, llm=llm or get_gemini_llm())
        except Exception as exc:  # noqa: BLE001 -- 失敗要因の種類は問わず「抽出失敗」として扱う
            return None, str(exc)
    else:
        return None, f"unsupported file_type: {file_type}"

    return text[:MAX_EXTRACTED_TEXT_CHARS], None


async def _extract_pdf_text(data: bytes, *, llm: _StructuredContentLLM) -> str:
    """PDFのバイト列をbase64データURIとしてGeminiに渡し、テキスト化した結果を返す。"""
    b64 = base64.b64encode(data).decode("ascii")
    message = HumanMessage(
        content=[
            {"type": "text", "text": _PDF_EXTRACTION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:application/pdf;base64,{b64}"}},
        ]
    )
    response = await llm.ainvoke([message])
    return extract_text_content(response.content)
