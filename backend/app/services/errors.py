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

    code: ClassVar[str | None] = "LLM_API_ERROR"

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

    code: ClassVar[str | None] = "UNSUPPORTED_FILE_TYPE"

class FileTooLargeError(BadRequestError):
    """添付ファイルが1ファイルあたりの上限(5MB)を超えている場合に送出する。"""

    code: ClassVar[str | None] = "FILE_TOO_LARGE"

class InvalidProjectNameError(BadRequestError):
    """プロジェクト名が空、または上限(40文字)を超えている場合に送出する。"""

    code: ClassVar[str | None] = "INVALID_PROJECT_NAME"

class LLMQuotaExceededError(TooManyRequestsError):
    """外部LLMプロバイダー(Gemini Flash-Lite無料枠)のトークン上限超過等で
    呼び出しが失敗した場合に送出する。
    app/services/chat.pyの既存の_is_quota_error判定と同じ基準をapp/services/llm_retry.pyに集約し、
    chat_service.py/doc_generator_service.pyの双方から利用する。"""

    code: ClassVar[str | None] = "LLM_QUOTA_EXCEEDED"

class PromptTemplateNotFoundError(NotFoundError):
    """プロジェクト作成時に指定されたtemplate_idが存在しない場合に送出する。"""

    code: ClassVar[str | None] = "RESOURCE_NOT_FOUND"

# UML設計図パイプライン
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


# レイアウトエンジン
class LayoutNodeLimitExceededError(BadRequestError):
    """`/layout`実行前のノード数上限チェック。
    要素数が多すぎる図は、レイアウトエンジンの計算量(O(n²)〜O(n!))が非現実的になるため、
    実行前に拒否する(`app/uml/validation/structural.py`のMAX_ELEMENTSを再利用)。"""

    code: ClassVar[str | None] = "LAYOUT_NODE_LIMIT_EXCEEDED"


class LayoutValidationFailedError(BadRequestError):
    """レイアウト対象の意味モデルが構造検証(ID重複・参照切れ等)を通らない場合に送出する。
    重複ID・未定義ノード参照は、レイアウトエンジン内でassertせず、この事前検証
    (`app.uml.validation.validate_diagram`)に一本化する。"""

    code: ClassVar[str | None] = "LAYOUT_VALIDATION_FAILED"


class LayoutWidthExceededError(BadRequestError):
    """レイアウト計算の結果、図の幅が上限(960px)を超えた場合に送出する
    (入力次第で実行時に起こり得るため、`assert`ではなく専用例外にする)。"""

    code: ClassVar[str | None] = "LAYOUT_WIDTH_EXCEEDED"


class LayoutRouteNotFoundError(BadRequestError):
    """辺の経路探索で有効な候補が1つも見つからなかった場合に送出する
    (入力次第で実行時に起こり得るため、`assert`ではなく専用例外にする)。"""

    code: ClassVar[str | None] = "LAYOUT_ROUTE_NOT_FOUND"


# UML図のAI生成


class UmlGenerationInProgressError(ConflictError):
    """プロジェクト内でUML図のAI生成が実行中の場合に送出する。生成はプロジェクトごとに1本に
    限る(並行生成によるデータ項目名の一意制約の競合と、無料枠クォータの浪費を避けるため)。
    生成中の図の更新(PUT)・レイアウト実行も、生成結果で上書きされるため同じ例外で拒否する。"""

    code: ClassVar[str | None] = "UML_GENERATION_IN_PROGRESS"


class LLMTokenLimitError(BadGatewayError):
    """LLMの入力または出力のトークン数が上限を超えた場合に送出する(出力が`MAX_TOKENS`で
    打ち切られた、または入力が大きすぎて拒否された)。同じ入力で再試行しても結果は変わらない
    ため、invoke_with_retryはこの例外をリトライせずにそのまま伝播させる。"""

    code: ClassVar[str | None] = "LLM_TOKEN_LIMIT"


class LLMInvalidOutputError(BadGatewayError):
    """LLMの構造化出力を出力スキーマとして解釈できなかった場合に送出する(出力の揺らぎによる
    一時的な失敗でありうるため、invoke_with_retryの通常のリトライ対象にする)。"""

    code: ClassVar[str | None] = "LLM_INVALID_OUTPUT"


# 承認フロー
class UmlDiagramNotApprovableError(ConflictError):
    """承認できない状態(承認済み・出力済み)の図を承認しようとした場合に送出する。
    承認し直すには、いったん保存してレビュー中へ戻す必要がある。"""

    code: ClassVar[str | None] = "UML_DIAGRAM_NOT_APPROVABLE"


class UmlApprovalValidationFailedError(BadRequestError):
    """承認しようとした図の意味モデルが検証を通らない場合に送出する。
    エラーの一覧は`POST .../validate`で取り直す(このエラーは件数と要約だけを返す)。"""

    code: ClassVar[str | None] = "UML_APPROVAL_VALIDATION_FAILED"


class UmlLayoutRequiredError(BadRequestError):
    """配置(`layout_model`)が無い図、または配置の無い要素を含む図を承認・出力しようとした場合に
    送出する。出力(draw.io/SVG)は座標が無いと描けないため、承認の条件にする。"""

    code: ClassVar[str | None] = "UML_LAYOUT_REQUIRED"


# draw.io/SVG出力
class UmlDiagramNotApprovedError(ConflictError):
    """承認されていない(下書き・レビュー中の)図を出力しようとした場合に送出する。
    出力は承認済みの図だけに許す(ダウンロードは最終成果物)。"""

    code: ClassVar[str | None] = "UML_DIAGRAM_NOT_APPROVED"


# 詳細設計モード
class DesignStagesNotAvailableError(ConflictError):
    """詳細設計モードでないプロジェクト(`projects.mode='simple'`)の段階を扱おうとした場合に送出する。
    モードは作成時に決まり、後から変えられない。"""

    code: ClassVar[str | None] = "DESIGN_STAGES_NOT_AVAILABLE"


class DesignStageNotFoundError(NotFoundError):
    """まだ作られていない(未着手の)段階を承認しようとした場合に送出する。"""

    code: ClassVar[str | None] = "RESOURCE_NOT_FOUND"


class DesignStageVersionConflictError(ConflictError):
    """段階の保存・承認時、リクエストのversionがDB上のversionと一致しない場合に送出する(楽観ロック)。"""

    code: ClassVar[str | None] = "VERSION_CONFLICT"


class DesignStageLockedError(ConflictError):
    """入力(前の段階の承認・文書)がそろっておらず、まだ開いていない段階を保存・承認しようとした
    場合に送出する。前の段階を承認すると次の段階が開く。"""

    code: ClassVar[str | None] = "DESIGN_STAGE_LOCKED"


class DesignStageNotApprovableError(ConflictError):
    """承認できない段階(承認済みで古くない・内容が空)を承認しようとした場合に送出する。"""

    code: ClassVar[str | None] = "DESIGN_STAGE_NOT_APPROVABLE"


class DesignStageInvalidError(ConflictError):
    """段階ごとの検証(app/detailed_design/validation.py)でエラーがある段階を承認しようとした場合に
    送出する。警告だけなら承認できる。指摘の一覧は段階の取得(`issues`)で確かめる。
    段階2で DFD を描くグループが上限を超えたまま生成を要求した場合にも送出する。"""

    code: ClassVar[str | None] = "DESIGN_STAGE_INVALID"


class DesignStageGenerationInProgressError(ConflictError):
    """段階の下書きを生成中に、その段階の生成・保存・承認を要求した場合に送出する。
    生成の結果で人の編集を上書きしないため、生成が終わるまで待たせる。"""

    code: ClassVar[str | None] = "DESIGN_STAGE_GENERATION_IN_PROGRESS"


class DesignStageGenerationNotSupportedError(ConflictError):
    """AIの下書きの生成にまだ対応していない段階の生成を要求した場合に送出する
    。"""

    code: ClassVar[str | None] = "DESIGN_STAGE_GENERATION_NOT_SUPPORTED"


class DocGenerationInProgressError(ConflictError):
    """設計書の生成が実行中(`projects.status='generating'`)のプロジェクトで、生成を再度要求した
    場合に送出する。二重実行で生成が並行し、版の番号が重複するのを防ぐ(画面側のボタンの無効化と
    二重で塞ぐ)。"""

    code: ClassVar[str | None] = "DOC_GENERATION_IN_PROGRESS"
