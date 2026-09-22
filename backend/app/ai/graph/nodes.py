from langchain_core.messages import AIMessage, HumanMessage

from app.ai.graph.state import GraphState
from app.ai.llm.gemini import get_gemini_llm
from app.ai.tools.tavily import get_tavily_search_tool
from app.schemas.generation import FinalAnswer


def draft_response(state: GraphState) -> dict:
    """ユーザーの質問に対してLLMで下書き回答を生成し、Web検索が必要かどうかを判定する。"""
    llm = get_gemini_llm()
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


def evaluate_search_results(state: GraphState) -> dict:
    """検索結果が質問への回答に十分かどうかをLLMに簡潔に評価させる。"""
    llm = get_gemini_llm(temperature=0)
    prompt = (
        f"Question: {state['question']}\n"
        f"Search results: {state['search_results']}\n\n"
        "Briefly evaluate whether these results are relevant and sufficient "
        "to answer the question."
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return {"evaluation": response.content}


def generate_final_answer(state: GraphState) -> dict:
    """検索結果と評価結果をもとに、構造化出力（FinalAnswer）で最終回答を生成する。"""
    llm = get_gemini_llm().with_structured_output(FinalAnswer)
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
    return {"answer": last_message.content}


def decide_to_search(state: GraphState) -> str:
    """needs_searchフラグを見て、検索ノードへ進むか直接終了するかの分岐先を返す。"""
    return "search" if state.get("needs_search") else "finalize"
