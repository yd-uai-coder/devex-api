from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from app.ai.graph.state import GraphState
from app.ai.llm.content import extract_text_content
from app.ai.llm.factory import STRUCTURED_METHOD
from app.ai.tools.tavily import get_tavily_search_tool
from app.schemas.generation import FinalAnswer


def llm_from(config: RunnableConfig, name: str = "llm") -> Any:
    """呼び出し側(app/services/chat.py)がユーザーごとに解決して`configurable`に入れた LLM。
    グラフはプロセスで1つにコンパイルして使い回すので、LLM はグラフに持たせず実行ごとに渡す。
    `evaluator_llm`は検索結果の評価に使う温度0の LLM。"""
    return config["configurable"][name]


def draft_response(state: GraphState, config: RunnableConfig) -> dict:
    """ユーザーの質問に対してLLMで下書き回答を生成し、Web検索が必要かどうかを判定する。"""
    llm = llm_from(config)
    response = llm.invoke([HumanMessage(content=state["question"])])
    return {
        "messages": [response],
        "needs_search": True,
        "search_query": state["question"],
    }


def web_search(state: GraphState) -> dict:
    """Tavily検索ツールを呼び出し、検索結果のリストを取得する。"""
    tool = get_tavily_search_tool()
    raw_results = tool.invoke({"query": state["search_query"]})
    # raw_resultsが辞書形式ならその"results"キーの中身を、それ以外はそのまま結果とする
    results = raw_results.get("results", []) if isinstance(raw_results, dict) else raw_results
    return {"search_results": results}


def evaluate_search_results(state: GraphState, config: RunnableConfig) -> dict:
    """検索結果が質問への回答に十分かどうかをLLMに簡潔に評価させる。"""
    llm = llm_from(config, "evaluator_llm")
    prompt = (
        f"Question: {state['question']}\n"
        f"Search results: {state['search_results']}\n\n"
        "Briefly evaluate whether these results are relevant and sufficient "
        "to answer the question."
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return {"evaluation": extract_text_content(response.content)}


def generate_final_answer(state: GraphState, config: RunnableConfig) -> dict:
    """検索結果と評価結果をもとに、構造化出力（FinalAnswer）で最終回答を生成する。"""
    llm = llm_from(config).with_structured_output(FinalAnswer, method=STRUCTURED_METHOD)
    prompt = (
        f"Question: {state['question']}\n"
        f"Search results: {state['search_results']}\n"
        f"Evaluation: {state['evaluation']}\n\n"
        "Using the above, write the final answer for the user."
    )
    # structured: FinalAnswerスキーマに沿って構造化されたLLM出力
    structured: FinalAnswer = llm.invoke([HumanMessage(content=prompt)])
    return {"answer": structured.answer, "messages": [AIMessage(content=structured.answer)]}


def finalize_without_search(state: GraphState) -> dict:
    """検索不要と判定された場合、直前のメッセージ内容をそのまま最終回答として扱う。"""
    last_message = state["messages"][-1]
    return {"answer": extract_text_content(last_message.content)}


def decide_to_search(state: GraphState) -> str:
    """needs_searchフラグを見て、検索ノードへ進むか直接終了するかの分岐先を返す。"""
    return "search" if state.get("needs_search") else "finalize"
