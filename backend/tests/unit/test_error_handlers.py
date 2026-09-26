from typing import ClassVar

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.error_handlers import register_error_handlers
from app.core.errors import BadRequestError, NotFoundError


class _CodedNotFoundError(NotFoundError):
    code: ClassVar[str | None] = "SOMETHING_NOT_FOUND"


def _build_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/no-code")
    async def _no_code() -> None:
        raise BadRequestError("bad request")

    @app.get("/with-code")
    async def _with_code() -> None:
        raise _CodedNotFoundError("missing")

    @app.get("/unexpected")
    async def _unexpected() -> None:
        raise RuntimeError("boom")

    return app


async def test_error_without_code_omits_code_field() -> None:
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/no-code")

    assert response.status_code == 400
    assert response.json() == {"detail": "bad request"}


async def test_error_with_code_includes_code_field() -> None:
    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/with-code")

    assert response.status_code == 404
    assert response.json() == {"detail": "missing", "code": "SOMETHING_NOT_FOUND"}


async def test_unexpected_exception_returns_internal_server_error() -> None:
    app = _build_app()
    # raise_app_exceptions=False: 既定ではhttpxのASGITransportはStarletteのServerErrorMiddleware
    # が再送出する例外をそのままテストコードへ伝播させる(サーバー側のバグを握りつぶさないための
    # httpxの意図的な既定動作)。ここではハンドラが返すJSONレスポンス自体を検証したいため無効化する。
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/unexpected")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "code": "INTERNAL_SERVER_ERROR"}
