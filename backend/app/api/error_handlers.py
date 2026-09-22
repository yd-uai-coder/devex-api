from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import AppError


def register_error_handlers(app: FastAPI) -> None:
    """AppErrorとそのサブクラスをまとめてJSONレスポンスに変換するハンドラをFastAPIへ登録する。"""

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        """AppError発生時に、例外クラスに紐づくstatus_codeとdetailメッセージを持つJSONを返す。"""
        # exc.status_code: 例外クラスごとに定義されたHTTPステータスコード
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})
