from functools import lru_cache

from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import settings


@lru_cache
def get_gemini_llm(*, temperature: float = 0.7) -> ChatGoogleGenerativeAI:
    """設定値から構築したChatGoogleGenerativeAIクライアントを、温度パラメータ単位でキャッシュして返す。"""
    return ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        api_key=settings.GOOGLE_API_KEY,
        temperature=temperature,
    )


def extract_text_content(content: str | list) -> str:
    """LangChainメッセージの`content`をプレーンな文字列に変換する。

    Geminiがthought signature(内部推論の継続性を保つためのメタデータ)を伴う応答を返すとき、
    `content`は`str`ではなく`[{"type": "text", "text": "...", "extras": {...}}]`のような
    辞書のリストになる(langchain-google-genaiの仕様)。素の`str()`はPythonのrepr(波括弧・
    引用符・エスケープされた改行)をそのまま出力してしまうため、`text`フィールドだけを
    取り出して連結する。chat_service.py(ヒアリング応答)・intake_file_processor.py(PDF
    テキスト化)・doc_generator_service.py(4文書生成+自己診断)の3箇所がGeminiの応答を
    テキスト化する際に共通で使う(CLAUDE.md #17: 実在の複数消費者による共通化)。
    """
    if isinstance(content, str):
        return content
    return "".join(
        str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in content
    )
