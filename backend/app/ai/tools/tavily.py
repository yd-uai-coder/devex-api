from functools import lru_cache

from langchain_tavily import TavilySearch

from app.core.config import settings


@lru_cache
def get_tavily_search_tool(*, max_results: int = 5) -> TavilySearch:
    """設定値から構築したTavily検索ツールを、最大結果件数単位でキャッシュして返す。"""
    return TavilySearch(
        max_results=max_results,
        tavily_api_key=settings.TAVILY_API_KEY,
    )
