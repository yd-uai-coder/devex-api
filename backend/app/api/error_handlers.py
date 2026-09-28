import sentry_sdk
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import AppError

logger = structlog.get_logger(__name__)

def register_error_handlers(app: FastAPI) -> None:
    """AppErrorとそのサブクラス、および未処理の例外をまとめてJSONレスポンスに変換するハンドラを
    FastAPIへ登録する。"""

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        """AppError発生時に、例外クラスに紐づくstatus_codeとdetailメッセージを持つJSONを返す。
        exc.codeが設定されている場合は`code`フィールドも追加する。
        """

        # exc.status_code: 例外クラスごとに定義されたHTTPステータスコード
        content: dict[str, object] = {"detail": str(exc)}
        if exc.code is not None:
            content["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        """AppErrorではない未処理の例外を拾う最終防衛ライン(docs/internal_design.md 3.4節
        `INTERNAL_SERVER_ERROR`)。詳細(スタックトレース等)はレスポンスに含めず、サーバーログにのみ
        記録する ── クライアントへの情報漏洩を防ぐため。

        ERRORレベルでスタックトレース付きログを記録し(内部設計書3.4節)、あわせて
        `sentry_sdk.capture_exception`を呼ぶ。`SENTRY_DSN`未設定時は`sentry_sdk.init`自体を
        呼んでいないため、この呼び出しは完全にno-op(送信先クライアントが無く即座に戻るだけ)。
        """

        logger.error("unhandled_exception", error_type=type(exc).__name__, exc_info=exc)
        sentry_sdk.capture_exception(exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "INTERNAL_SERVER_ERROR"},
        )

