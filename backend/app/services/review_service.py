"""設計レビュー(生成とは別のモデルが、文書の指摘だけを返す)のユースケース。

docs/internal_design.md 3.3節「8. AI 設計レビュー + テスト設計」。受け付け(`start_documents_review`:
使えるか・実行中でないか・上限)と実行(`execute`: LLM を呼んで指摘を保存)を分ける。受け付けは
リクエストの中で、実行はバックグラウンドで行うため(生成と同じ形)。レビューの失敗は回に記録し、
例外にしない(生成やチャットを止めない)。
"""

import uuid
from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.claude import get_review_llm
from app.core.config import settings
from app.core.errors import AppError
from app.hearing import as_scale
from app.models.project import Project
from app.models.review import ReviewRun
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import DOC_TYPES_BY_MODE, GeneratedDocumentRepository
from app.repositories.hearing_point import HearingPointRepository
from app.repositories.review import ReviewRunRepository
from app.review import (
    ReviewOutput,
    build_review_messages,
    known_issues,
    render_findings,
    review_effective,
)
from app.services.errors import (
    DocGenerationInProgressError,
    RateLimitExceededError,
    ReviewDocumentsNotReadyError,
    ReviewInProgressError,
    ReviewNotAllowedError,
    ReviewRateLimitExceededError,
    ReviewUnavailableError,
)
from app.services.generation_staleness import is_stale
from app.services.llm_retry import invoke_structured_with_usage
from app.services.rate_limit import RateLimit, RateLimiter

logger = structlog.get_logger(__name__)

# 文書の生成の後の区切り(簡易は4文書、詳細は要件・外部)
DOCUMENTS_GATE = "documents"
# Claude の構造化出力は JSON Schema で受ける(ツール呼び出しを強制する方式は推奨されない)
_STRUCTURED_METHOD = "json_schema"
# 例外に code の無い失敗(想定外の例外)の理由
_FAILED_CODE = "REVIEW_FAILED"
_STALE_CODE = "STALE_REVIEW"

_FAILED_MESSAGE = "設計レビューに失敗しました。時間をおいて「もう一度レビュー」を押してください。"


def failure_code(exc: BaseException) -> str:
    """失敗の理由(回の error_code)。アプリの例外はその code、それ以外は REVIEW_FAILED。"""
    if isinstance(exc, AppError) and exc.code:
        return exc.code
    return _FAILED_CODE


class ReviewService:
    """設計レビューの受け付け・実行・結果の取得。"""

    def __init__(
        self, session: AsyncSession, *, redis: Redis | None = None, llm=None
    ) -> None:
        # redis: 1日の回数の上限を数える(受け付けで使う)。llm: テストで差し替える
        self._session = session
        self._runs = ReviewRunRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._hearing_points = HearingPointRepository(session)
        self._chat_histories = ChatHistoryRepository(session)
        self._redis = redis
        self._llm = llm

    async def start_documents_review(
        self, project: Project, user: User, *, trigger: str
    ) -> ReviewRun:
        """文書の区切りのレビューを受け付け、実行中の回を作って commit する。

        使えない(superuser でない・設定がオフ)は403、文書の生成中・レビューの実行中・文書が
        そろっていないは409、API キーが無いは502、1日の上限は429。上限は、レビューのモデルを
        実際に呼ぶ回だけを数える(使えない・キーが無いときは数えない)。"""
        if not review_effective(
            is_superuser=user.is_superuser, review_enabled=user.review_enabled
        ):
            raise ReviewNotAllowedError("設計レビューを使えない設定です。")
        if trigger == "manual" and project.status == "generating":
            raise DocGenerationInProgressError(
                "設計書を生成しています。完了してから再度お試しください。"
            )
        await self.recover_stale(project.id)
        if await self._runs.list_running(project.id):
            raise ReviewInProgressError("設計レビューを実行しています。")
        targets = await self.document_targets(project)
        if targets is None:
            raise ReviewDocumentsNotReadyError("レビューする設計書がまだありません。")
        if not settings.E2E_FAKE_LLM and not settings.ANTHROPIC_API_KEY and self._llm is None:
            raise ReviewUnavailableError("設計レビューのAPIキーが設定されていません。")
        await self._enforce_limit(user)
        run = await self._runs.create_run(
            project_id=project.id,
            gate=DOCUMENTS_GATE,
            targets=targets,
            trigger=trigger,
            model=settings.REVIEW_MODEL,
        )
        await self._session.commit()
        return run

    async def record_failed_start(
        self, project: Project, *, trigger: str, exc: BaseException
    ) -> ReviewRun:
        """受け付けで止まった自動のレビューを、失敗の回として残し、理由をチャットに出す
        (「もう一度レビュー」の目印)。"""
        run = await self._runs.create_run(
            project_id=project.id,
            gate=DOCUMENTS_GATE,
            targets=await self.document_targets(project) or {},
            trigger=trigger,
            model=settings.REVIEW_MODEL,
            status="failed",
        )
        await self._runs.finish(run, status="failed", error_code=failure_code(exc))
        reason = str(exc) if isinstance(exc, AppError) else _FAILED_MESSAGE
        message = f"設計レビューを流せませんでした: {reason}"
        await self._chat_histories.add(project_id=project.id, sender="others", message=message)
        await self._session.commit()
        return run

    async def execute(self, run_id: uuid.UUID) -> ReviewRun | None:
        """実行中の回のレビューを流し、指摘を保存して commit する。失敗は回に記録して返す
        (例外にしない)。結果はチャットにも出す(成功は指摘の md、失敗は「失敗しました」)。
        回が無い・実行中でないときは None。"""
        run = await self._runs.find_one(id=run_id)
        if run is None or run.status != "running":
            return None
        project = await self._session.get(Project, run.project_id)
        if project is None:
            return None
        try:
            documents: dict[str, str] = {}
            models: dict[str, dict | None] = {}
            for doc_type, version in run.targets.items():
                document = await self._documents.get_version(
                    project_id=project.id, doc_type=doc_type, version=version
                )
                if document is None:
                    raise ReviewDocumentsNotReadyError(f"{doc_type} v{version} not found")
                documents[doc_type] = document.content
                models[doc_type] = document.model
            states = await self._hearing_points.load_states(project.id)
            messages = build_review_messages(
                documents, states, as_scale(project.hearing_scale), known_issues(models)
            )
            llm = self._llm or get_review_llm()
            output, usage = await invoke_structured_with_usage(
                llm, ReviewOutput, messages, method=_STRUCTURED_METHOD
            )
        except Exception as exc:  # noqa: BLE001 -- どの失敗も回に記録し、生成・チャットを止めない
            if isinstance(exc, SQLAlchemyError):
                # LLM の失敗では書きかけが無いので戻さない(戻すと呼び出し側のオブジェクトまで
                # 失効させる)。DB の失敗のときだけ戻して、回を読み直す
                await self._session.rollback()
                run = await self._runs.find_one(id=run_id)
                if run is None:
                    return None
            await self._runs.finish(run, status="failed", error_code=failure_code(exc))
            await self._chat_histories.add(
                project_id=run.project_id, sender="others", message=_FAILED_MESSAGE
            )
            await self._session.commit()
            logger.warning(
                "design_review_failed",
                project_id=str(run.project_id),
                error_type=type(exc).__name__,
                error_code=run.error_code,
            )
            return run
        await self._runs.finish(run, status="succeeded", usage=usage, findings=output.findings)
        await self._chat_histories.add(
            project_id=project.id, sender="others", message=render_findings(output.findings)
        )
        await self._session.commit()
        logger.info(
            "design_review_succeeded",
            project_id=str(project.id),
            findings=len(output.findings),
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
        )
        return run

    async def latest_documents_review(self, project: Project) -> ReviewRun | None:
        """文書の区切りの最新の回(指摘付き)。止まった実行中の回は先に失敗に戻す。"""
        if await self.recover_stale(project.id):
            await self._session.commit()
        return await self._runs.get_latest(project_id=project.id, gate=DOCUMENTS_GATE)

    async def document_targets(self, project: Project) -> dict[str, int] | None:
        """モードの文書の、表示中の版 {doc_type: version}。1つでも無ければ None。"""
        targets: dict[str, int] = {}
        for doc_type in DOC_TYPES_BY_MODE[project.mode]:
            current = await self._documents.get_current(project_id=project.id, doc_type=doc_type)
            if current is None:
                return None
            targets[doc_type] = current.version
        return targets

    async def recover_stale(self, project_id: uuid.UUID, *, now: datetime | None = None) -> bool:
        """しきい値(15分。app/services/generation_staleness.py)を超えて実行中の回を失敗に
        戻す(プロセスの再起動などで止まった回が、以後のレビューを塞がないように)。flushまで。"""
        recovered = False
        for run in await self._runs.list_running(project_id):
            if is_stale(run.started_at, now or datetime.now(UTC)):
                await self._runs.finish(run, status="failed", error_code=_STALE_CODE)
                recovered = True
        return recovered

    async def _enforce_limit(self, user: User) -> None:
        """superuser ごとの1日の回数を数える(Redis を渡さない呼び出しでは数えない)。"""
        if self._redis is None:
            return
        per_day = RateLimit(window_seconds=86400, max_requests=settings.REVIEW_RATE_LIMIT_PER_DAY)
        limiter = RateLimiter(self._redis, resource="design_review", limits=[per_day])
        try:
            await limiter.enforce(str(user.id))
        except RateLimitExceededError as exc:
            raise ReviewRateLimitExceededError(
                "設計レビューの1日の回数の上限に達しました。明日また試してください。"
            ) from exc
