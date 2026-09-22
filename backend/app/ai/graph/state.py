from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class GraphState(TypedDict):
    """チャットワークフロー全体で引き回される状態。

    処理の流れ: ユーザー -> Gemini -> Tavily -> 検索結果評価 -> Gemini -> 最終回答。
    """

    messages: Annotated[list[BaseMessage], add_messages]
    question: str
    needs_search: bool
    search_query: str
    search_results: list[dict]
    evaluation: str
    answer: str
