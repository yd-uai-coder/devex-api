from langchain_core.messages import AIMessage

from app.ai.graph import nodes
from app.schemas.generation import FinalAnswer


class FakeLLM:
    """Gemini LLMクライアントの挙動を模したテスト用スタブ。"""

    def __init__(self, content: str | None = None, structured: FinalAnswer | None = None) -> None:
        # content: invoke()が返すAIMessageの本文
        # structured: with_structured_output().invoke()が返す構造化レスポンス
        self._content = content
        self._structured = structured

    def invoke(self, _messages):
        """通常のinvoke呼び出しの結果としてAIMessageを返す。"""
        return AIMessage(content=self._content)

    def with_structured_output(self, _schema):
        """構造化出力用のサブクライアントを返す。"""
        return _FakeStructuredLLM(self._structured)


class _FakeStructuredLLM:
    """with_structured_output()が返す、構造化レスポンスのみを返すテスト用スタブ。"""

    def __init__(self, structured: FinalAnswer | None) -> None:
        self._structured = structured

    def invoke(self, _messages):
        """構造化済みレスポンス（FinalAnswerインスタンス）をそのまま返す。"""
        return self._structured


class FakeSearchTool:
    """Tavily検索ツールの挙動を模したテスト用スタブ。"""

    def __init__(self, results):
        self._results = results

    def invoke(self, _args):
        """検索結果をTavily APIと同じ辞書形式で返す。"""
        return {"results": self._results}


def test_decide_to_search_returns_search_when_needed() -> None:
    assert nodes.decide_to_search({"needs_search": True}) == "search"


def test_decide_to_search_returns_finalize_when_not_needed() -> None:
    assert nodes.decide_to_search({"needs_search": False}) == "finalize"


def test_finalize_without_search_uses_last_message_content() -> None:
    state = {"messages": [AIMessage(content="direct answer")]}

    result = nodes.finalize_without_search(state)

    assert result == {"answer": "direct answer"}


def test_draft_response_invokes_llm_and_flags_search(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "get_gemini_llm", lambda **_: FakeLLM(content="draft"))

    result = nodes.draft_response({"question": "What is LangGraph?"})

    assert result["needs_search"] is True
    assert result["search_query"] == "What is LangGraph?"
    assert result["messages"][0].content == "draft"


def test_web_search_extracts_results_from_tool_response(monkeypatch) -> None:
    monkeypatch.setattr(
        nodes, "get_tavily_search_tool", lambda **_: FakeSearchTool([{"url": "https://x"}])
    )

    result = nodes.web_search({"search_query": "langgraph"})

    assert result["search_results"] == [{"url": "https://x"}]


def test_evaluate_search_results_returns_llm_content(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "get_gemini_llm", lambda **_: FakeLLM(content="looks relevant"))

    result = nodes.evaluate_search_results(
        {"question": "q", "search_results": [{"url": "https://x"}]}
    )

    assert result == {"evaluation": "looks relevant"}


def test_generate_final_answer_returns_structured_content(monkeypatch) -> None:
    monkeypatch.setattr(
        nodes,
        "get_gemini_llm",
        lambda **_: FakeLLM(structured=FinalAnswer(answer="final answer")),
    )

    result = nodes.generate_final_answer(
        {"question": "q", "search_results": [], "evaluation": "ok"}
    )

    assert result["answer"] == "final answer"
    assert result["messages"][0].content == "final answer"
