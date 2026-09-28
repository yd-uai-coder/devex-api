from typing import ClassVar

import pytest
import structlog.testing
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import error_handlers
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

async def test_unexpected_exception_logs_error_and_calls_sentry_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ERRORレベルでログを記録し、sentry_sdk.capture_exceptionを呼ぶことを確認する。
    SENTRY_DSN未設定(=sentry_sdk.init未実行)でもcapture_exception自体はno-opとして
    安全に呼べるため、実際にSentryへ送信されるかまでは検証しない(呼び出し自体の確認に留める)。"""
    captured: list[Exception] = []
    monkeypatch.setattr(error_handlers.sentry_sdk, "capture_exception", captured.append)
    app = _build_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    with structlog.testing.capture_logs() as logs:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.get("/unexpected")

    assert len(captured) == 1
    assert isinstance(captured[0], RuntimeError)
    error_logs = [log for log in logs if log["log_level"] == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["event"] == "unhandled_exception"
