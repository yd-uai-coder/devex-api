from collections.abc import AsyncGenerator

import httpx

_DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


async def get_http_client() -> AsyncGenerator[httpx.AsyncClient]:
    """外部HTTP呼び出し用に、リクエスト単位の非同期HTTPクライアントを生成するFastAPI依存関数。"""
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        yield client
