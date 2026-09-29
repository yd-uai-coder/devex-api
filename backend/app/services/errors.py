from typing import ClassVar

from app.core.errors import (
    BadGatewayError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    TooManyRequestsError,
    UnauthorizedError,
)


class InvalidCredentialsError(UnauthorizedError):
    """メールアドレスまたはパスワードが誤っている、もしくは無効化済みユーザーの場合に送出する。"""


class InvalidTokenError(UnauthorizedError):
    """JWTが不正・期限切れ、またはRedis上で失効済みの場合に送出する。"""


class UserAlreadyExistsError(ConflictError):
    """登録しようとしたメールアドレスが既に別ユーザーで使用されている場合に送出する。"""


class ConversationNotFoundError(NotFoundError):
    """指定した会話IDが存在しない、または他ユーザーが所有する会話である場合に送出する。"""


class RateLimitExceededError(TooManyRequestsError):
    """Redisで管理するレート制限の上限（時間/日単位など）を超過した場合に送出する。"""


class GenerationFailedError(BadGatewayError):
    """LLM呼び出しが規定回数のリトライ後も失敗し続けた場合に送出する。"""

class ProjectNotFoundError(NotFoundError):
    """指定したプロジェクトIDが存在しない、または他ユーザーが所有するプロジェクトである場合に送出する。"""

    code: ClassVar[str | None] = "RESOURCE_NOT_FOUND"

class DocumentNotFoundError(NotFoundError):
    """指定したドキュメントIDが存在しない、または他ユーザーが所有するプロジェクトのものである場合に送出する。"""

    code: ClassVar[str | None] = "RESOURCE_NOT_FOUND"

class TooManyFilesError(BadRequestError):
    """初期ヒアリングの添付ファイルが上限(3件)を超えている場合に送出する。"""

    code: ClassVar[str | None] = "TOO_MANY_FILES"

class UnsupportedFileTypeError(BadRequestError):
    """添付ファイルがtxt/Markdown/PDF以外の形式である場合に送出する。"""

class FileTooLargeError(BadRequestError):
    """添付ファイルが1ファイルあたりの上限(5MB)を超えている場合に送出する。"""

    code: ClassVar[str | None] = "FILE_TOO_LARGE"

class LLMQuotaExceededError(TooManyRequestsError):
    """外部LLMプロバイダー(Gemini Flash-Lite無料枠)のトークン上限超過等で
    呼び出しが失敗した場合に送出する。
    app/services/chat.pyの既存の_is_quota_error判定と同じ基準をapp/services/llm_retry.pyに集約し、
    chat_service.py/doc_generator_service.pyの双方から利用する。"""

    code: ClassVar[str | None] = "LLM_QUOTA_EXCEEDED"

class PromptTemplateNotFoundError(NotFoundError):
    """プロジェクト作成時に指定されたtemplate_idが存在しない場合に送出する。"""

    code: ClassVar[str | None] = "RESOURCE_NOT_FOUND"

# Phase-8-2:追記 ── ステージ3(UML設計図パイプライン)
class UmlDiagramNotFoundError(NotFoundError):
    """指定したUML図IDが存在しない、または他プロジェクトのものである場合に送出する。"""

    code: ClassVar[str | None] = "RESOURCE_NOT_FOUND"

class DataItemNotFoundError(NotFoundError):
    """指定したデータ項目IDが存在しない、または他プロジェクトのものである場合に送出する。"""

    code: ClassVar[str | None] = "RESOURCE_NOT_FOUND"

class UmlDiagramVersionConflictError(ConflictError):
    """UML図の更新(PUT)時、リクエストのversionがDB上の最新versionと一致しない場合に送出する
    (楽観ロック)。既存コードベースに前例の無い新規パターン(generated_documents.versionは
    「再生成のたびに増える版数」であり、書き込み競合検知の仕組みではない)。"""

    code: ClassVar[str | None] = "VERSION_CONFLICT"

class DataItemNameConflictError(ConflictError):
    """同一プロジェクト内に同名のデータ項目が既に存在する場合に送出する(data_items.name一意制約)。"""

    code: ClassVar[str | None] = "DATA_ITEM_NAME_CONFLICT"
