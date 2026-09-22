from typing import ClassVar


class AppError(Exception):
    """アプリケーション全体で共通のドメイン例外基底クラス。HTTPステータスコードを紐づけて一括ハンドリングする。"""

    status_code: ClassVar[int] = 500

    def __init__(self, message: str) -> None:
        # message: クライアントに返すエラーメッセージ文字列
        super().__init__(message)


class BadRequestError(AppError):
    """リクエスト内容が不正な場合に送出する例外（HTTP 400に対応）。"""

    status_code: ClassVar[int] = 400


class UnauthorizedError(AppError):
    """認証情報が無効・不足している場合に送出する例外（HTTP 401に対応）。"""

    status_code: ClassVar[int] = 401


class ForbiddenError(AppError):
    """認証済みだが権限が不足している場合に送出する例外（HTTP 403に対応）。"""

    status_code: ClassVar[int] = 403


class NotFoundError(AppError):
    """指定されたリソースが存在しない場合に送出する例外（HTTP 404に対応）。"""

    status_code: ClassVar[int] = 404


class ConflictError(AppError):
    """既存リソースと状態が競合する場合に送出する例外（HTTP 409に対応）。"""

    status_code: ClassVar[int] = 409


class TooManyRequestsError(AppError):
    """レート制限の上限に達した場合に送出する例外（HTTP 429に対応）。"""

    status_code: ClassVar[int] = 429


class BadGatewayError(AppError):
    """外部サービス（LLMや検索APIなど）呼び出しが失敗した場合に送出する例外（HTTP 502に対応）。"""

    status_code: ClassVar[int] = 502
