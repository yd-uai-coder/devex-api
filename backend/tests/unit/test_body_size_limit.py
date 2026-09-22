"""`BodySizeLimitMiddleware`。

テスト対象 / ドライバ / スタブ:
- 対象: `app.api.middleware.BodySizeLimitMiddleware`
- ドライバ: httpx の `ASGITransport` で最小の FastAPI アプリを直接呼ぶ
- スタブ不要 ── DB・Redis を使わない、ミドルウェア単体の検証
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request

from app.api.middleware import BodySizeLimitMiddleware

_LIMIT = 100


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=_LIMIT)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    return app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://t")


async def test_body_within_limit_passes() -> None:
    async with _client() as client:
        res = await client.post("/echo", content=b"x" * _LIMIT)
    assert res.status_code == 200
    assert res.json() == {"size": _LIMIT}


async def test_declared_content_length_over_limit_is_rejected_with_413() -> None:
    async with _client() as client:
        res = await client.post("/echo", content=b"x" * (_LIMIT + 1))
    assert res.status_code == 413
    assert res.json() == {"detail": "request body too large"}


async def test_chunked_body_without_content_length_is_rejected_when_it_grows_too_large() -> None:
    """Content-Length が無い(チャンク転送)場合も、受信したバイト数を数えて拒否する。"""

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(5):
            yield b"x" * 40  # 合計 200 バイト > 100

    async with _client() as client:
        res = await client.post("/echo", content=chunks())
    assert res.status_code == 413
