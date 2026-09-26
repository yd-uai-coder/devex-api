import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import AppError

logger = logging.getLogger(__name__)

def register_error_handlers(app: FastAPI) -> None:
    """AppErrorとそのサブクラス、および未処理の例外をまとめてJSONレスポンスに変換するハンドラを
    FastAPIへ登録する。"""

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        """AppError発生時に、例外クラスに紐づくstatus_codeとdetailメッセージを持つJSONを返す。
        exc.codeが設定されている場合は`code`フィールドも追加する。"""
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

        既知の簡略化: ログ出力は標準`logging`のみで、docs/internal_design.md 3.4節が言及する
        JSON構造化ログ(`structlog`等)への統一は未実施(今後のPhaseでの課題)。"""
        logger.exception("Unhandled exception", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "INTERNAL_SERVER_ERROR"},
        )
