"""設計レビュー(生成とは別のモデルが、文書の指摘だけを返す)のユースケース。

docs/internal_design.md 3.3節「8. AI 設計レビュー + テスト設計」。受け付け(`start_review`:
使えるか・実行中でないか・区切りがそろっているか・上限)と実行(`execute`: LLM を呼んで指摘を保存)を
分ける。受け付けはリクエストの中で、実行はバックグラウンドで行うため(生成と同じ形)。レビューの
失敗は回に記録し、例外にしない(生成やチャットを止めない)。

区切り(app/review/gates.py)は、文書の生成の後と、詳細設計モードの段階6・7の承認の後。段階の区切りは
上流の要件・外部と、詳細設計書(・実装計画書)の md を渡し、段階の検証の指摘も既知として渡す。
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import structlog
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.claude import get_review_llm
from app.core.config import settings
from app.core.errors import AppError
from app.detailed_design.document import CHAPTERS, to_markdown, to_plan_markdown
from app.detailed_design.plan import PLAN_STAGE
from app.detailed_design.stages import stage_key
from app.detailed_design.validation import validate_stage
from app.documents.issues import DocumentIssue
from app.hearing import as_scale
from app.models.project import Project
from app.models.review import ReviewFinding, ReviewFixCheck, ReviewRun
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import DOC_TYPES_BY_MODE, GeneratedDocumentRepository
from app.repositories.hearing_point import HearingPointRepository
from app.repositories.review import ReviewFixCheckRepository, ReviewRunRepository
from app.review import (
    DETAILED_DESIGN_DOC,
    DETAILED_PLAN_DOC,
    DOCUMENTS_GATE,
    GATE_LABEL,
    GATE_STAGES,
    FollowupState,
    GateStatus,
    ReviewGate,
    ReviewOutput,
    RowDiff,
    SendbackFinding,
    build_review_messages,
    can_regenerate,
    can_rehear,
    document_model_rows,
    element_rows,
    followup_state,
    gate_status,
    gates_for_mode,
    known_issues,
    only_reviewed_documents,
    rehearing_message,
    rehearing_points,
    render_notice,
    review_effective,
    stage_known_issues,
    stage_targets,
    version_keys,
)
from app.schemas.hearing import HearingPointsRead
from app.services.chat_service import ChatService
from app.services.design_stage_service import DesignStageService
from app.services.detailed_design_export_service import DetailedDesignExportService
from app.services.errors import (
    DocGenerationInProgressError,
    RateLimitExceededError,
    RehearingFindingInvalidError,
    ReviewDocumentsNotReadyError,
    ReviewFindingNotFoundError,
    ReviewInProgressError,
    ReviewNotAllowedError,
    ReviewRateLimitExceededError,
    ReviewUnavailableError,
    SendbackFindingInvalidError,
)
from app.services.generation_staleness import is_stale
from app.services.llm_retry import invoke_structured_with_usage
from app.services.project_deletion import commit_if_live, project_vanished
from app.services.rate_limit import RateLimit, RateLimiter

logger = structlog.get_logger(__name__)

# Claude の構造化出力は JSON Schema で受ける(ツール呼び出しを強制する方式は推奨されない)
_STRUCTURED_METHOD = "json_schema"
# 例外に code の無い失敗(想定外の例外)の理由
_FAILED_CODE = "REVIEW_FAILED"
_STALE_CODE = "STALE_REVIEW"

_FAILED_MESSAGE = "設計レビューに失敗しました。時間をおいて「もう一度レビュー」を押してください。"
_NOT_READY_MESSAGE: dict[str, str] = {
    "documents": "レビューする設計書がまだありません。",
    "stage6": "段階1〜6が承認されていないため、詳細設計をレビューできません。",
    "stage7": "段階1〜7が承認されていないため、全文書をレビューできません。",
}
# 回の版のうち、生成した文書の種類(それ以外は段階の承認した版 "stage:N")
_DOCUMENT_TYPES = frozenset(DOC_TYPES_BY_MODE["simple"]) | frozenset(DOC_TYPES_BY_MODE["detailed"])


def failure_code(exc: BaseException) -> str:
    """失敗の理由(回の error_code)。アプリの例外はその code、それ以外は REVIEW_FAILED。"""
    if isinstance(exc, AppError) and exc.code:
        return exc.code
    return _FAILED_CODE


@dataclass(frozen=True)
class GateSummary:
    """区切り1つ分の、画面に出す状態。`run`は最新の回(無ければ None)、`is_current`はその回が
    今の版をレビューしたものか。"""

    gate: ReviewGate
    ready: bool
    status: GateStatus
    run: ReviewRun | None
    is_current: bool
    # 前の回で採用した指摘(新しい回から。再生成の後の自動のレビューで最新の回が替わっても、
    # 採用した指摘を見失わないように)
    adopted_before: Sequence[ReviewFinding] = ()
    # 採用した指摘の追跡の状態 {指摘の id: 状態}(最新の回・前の回の採用した指摘)
    followups: Mapping[uuid.UUID, FollowupState] = field(default_factory=dict)
    # 採用した指摘の、今の版についての「直ったか」の判定(シャドーモード。無ければ入らない)
    fix_checks: Mapping[uuid.UUID, ReviewFixCheck] = field(default_factory=dict)


class ReviewService:
    """設計レビューの受け付け・実行・結果の取得。"""

    def __init__(
        self, session: AsyncSession, *, redis: Redis | None = None, llm=None
    ) -> None:
        # redis: 1日の回数の上限を数える(受け付けで使う)。llm: テストで差し替える
        self._session = session
        self._runs = ReviewRunRepository(session)
        self._fix_checks = ReviewFixCheckRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._hearing_points = HearingPointRepository(session)
        self._stages = DesignStageRepository(session)
        self._chat_histories = ChatHistoryRepository(session)
        self._redis = redis
        self._llm = llm

    async def start_review(
        self, project: Project, user: User, *, gate: ReviewGate, trigger: str
    ) -> ReviewRun:
        """区切りのレビューを受け付け、実行中の回を作って commit する。

        使えない(superuser でない・設定がオフ)は403、文書の生成中・レビューの実行中・区切りが
        そろっていない(文書が無い・段階が承認されていない・モードに無い区切り)は409、API キーが
        無いは502、1日の上限は429。上限は、レビューのモデルを実際に呼ぶ回だけを数える(使えない・
        キーが無いときは数えない)。"""
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
        targets = await self.targets_for(project, gate)
        if targets is None:
            raise ReviewDocumentsNotReadyError(_NOT_READY_MESSAGE[gate])
        if not settings.E2E_FAKE_LLM and not settings.ANTHROPIC_API_KEY and self._llm is None:
            raise ReviewUnavailableError("設計レビューのAPIキーが設定されていません。")
        await self._enforce_limit(user)
        run = await self._runs.create_run(
            project_id=project.id,
            gate=gate,
            targets=targets,
            trigger=trigger,
            model=settings.REVIEW_MODEL,
            snapshots=await self._stage_snapshots(project, gate),
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
        (例外にしない)。結果はチャットにも出す(成功は件数の一行、失敗は「失敗しました」)。
        回が無い・実行中でないときは None。プロジェクトが削除された(印が付いた)ときも、結果も失敗も
        記録せずに None(app/services/project_deletion.py)。"""
        run = await self._runs.find_one(id=run_id)
        if run is None or run.status != "running":
            return None
        project_id = run.project_id
        if await project_vanished(self._session, project_id, job="review"):
            return None
        project = await self._session.get(Project, project_id)
        if project is None:
            return None
        try:
            documents: dict[str, str] = {}
            models: dict[str, dict | None] = {}
            for doc_type, version in run.targets.items():
                if doc_type not in _DOCUMENT_TYPES:
                    continue  # 段階の承認した版(段階の内容は下で詳細設計書として渡す)
                document = await self._documents.get_version(
                    project_id=project.id, doc_type=doc_type, version=version
                )
                if document is None:
                    raise ReviewDocumentsNotReadyError(f"{doc_type} v{version} not found")
                documents[doc_type] = document.content
                models[doc_type] = document.model
            issues = known_issues(models)
            if run.gate != DOCUMENTS_GATE:
                issues += await self._add_stage_documents(project, run.gate, documents)
            states = await self._hearing_points.load_states(project.id)
            messages = build_review_messages(
                documents, states, as_scale(project.hearing_scale), issues
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
            if await project_vanished(self._session, project_id, job="review"):
                return None  # レビューの間に削除された。失敗として記録しない
            await self._runs.finish(run, status="failed", error_code=failure_code(exc))
            await self._chat_histories.add(
                project_id=run.project_id, sender="others", message=_FAILED_MESSAGE
            )
            if not await commit_if_live(self._session, project_id, job="review"):
                return None
            logger.warning(
                "design_review_failed",
                project_id=str(run.project_id),
                error_type=type(exc).__name__,
                error_code=run.error_code,
            )
            return run
        findings = only_reviewed_documents(output.findings, documents.keys())
        notice = render_notice(output.findings, GATE_LABEL[cast(ReviewGate, run.gate)])

        async def _save() -> None:
            await self._runs.finish(run, status="succeeded", usage=usage, findings=findings)
            await self._chat_histories.add(project_id=project_id, sender="others", message=notice)

        if not await commit_if_live(self._session, project_id, job="review", write=_save):
            return None
        logger.info(
            "design_review_succeeded",
            project_id=str(project.id),
            gate=run.gate,
            findings=len(output.findings),
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
        )
        return run

    async def latest(self, project: Project, gate: ReviewGate) -> ReviewRun | None:
        """区切りの最新の回(指摘付き)。止まった実行中の回は先に失敗に戻す。"""
        if await self.recover_stale(project.id):
            await self._session.commit()
        return await self._runs.get_latest(project_id=project.id, gate=gate)

    async def gate_summaries(self, project: Project) -> list[GateSummary]:
        """モードの区切りごとの状態(区切りの順)。段階の承認した版は1回だけ読む。"""
        if await self.recover_stale(project.id):
            await self._session.commit()
        gates = gates_for_mode(project.mode)
        all_targets = await self._all_targets(project, gates)
        summaries: list[GateSummary] = []
        for gate in gates:
            targets = all_targets[gate]
            run = await self._runs.get_latest(project_id=project.id, gate=gate)
            is_current = run is not None and targets is not None and run.targets == targets
            status = gate_status(
                ready=targets is not None,
                run_status=run.status if run else None,
                is_current=is_current,
            )
            before = [
                r
                for r in await self._runs.list_with_adopted(project_id=project.id, gate=gate)
                if run is None or r.id != run.id
            ]
            adopted_before = [f for r in before for f in r.findings if f.decision == "adopted"]
            runs = [*([run] if run else []), *before]
            followups = await self.followups(project, runs)
            fix_checks = await self.current_fix_checks(project, runs)
            summaries.append(
                GateSummary(
                    gate,
                    targets is not None,
                    status,
                    run,
                    is_current,
                    adopted_before,
                    followups,
                    fix_checks,
                )
            )
        return summaries

    async def decide(
        self, project: Project, finding_id: uuid.UUID, decision: str
    ) -> ReviewFinding:
        """指摘の採否(pending / adopted / rejected / held)を記録して commit する。プロジェクトに
        無い指摘は404。採否は回の版によらず付けられる(古い回の指摘を見て決めてもよい)。"""
        finding = await self._runs.get_finding(project_id=project.id, finding_id=finding_id)
        if finding is None:
            raise ReviewFindingNotFoundError("指摘が見つかりません。")
        finding.decision = decision
        await self._session.commit()
        return finding

    async def followups(
        self, project: Project, runs: Iterable[ReviewRun]
    ) -> dict[uuid.UUID, FollowupState]:
        """回の採用した指摘ごとの追跡の状態(app/review/followup.py)。版の変わった文書・段階の
        行は、回ごとに1回だけ読む。"""
        current = await self.current_versions(project)
        result: dict[uuid.UUID, FollowupState] = {}
        for run in runs:
            adopted = [f for f in run.findings if f.decision == "adopted"]
            if not adopted:
                continue
            diffs = await self.row_diffs(project, run, current)
            for finding in adopted:
                result[finding.id] = followup_state(
                    documents=finding.documents,
                    targets=finding.targets,
                    reviewed=run.targets,
                    current=current,
                    diffs=diffs,
                )
        return result

    async def sendback_findings(
        self, project_id: uuid.UUID, finding_ids: Sequence[uuid.UUID], *, strict: bool = False
    ) -> list[SendbackFinding]:
        """再生成の指示に入れる指摘(渡した順、重ねない)。`strict`(受け付け)では、プロジェクトに
        無い指摘を404、採用していない・ヒアリングが原因の指摘を409にする。生成の中では、受け付けの
        後に採否が変わった指摘を黙って外す。"""
        findings: list[SendbackFinding] = []
        for finding_id in dict.fromkeys(finding_ids):
            finding = await self._runs.get_finding(project_id=project_id, finding_id=finding_id)
            if finding is None:
                if strict:
                    raise ReviewFindingNotFoundError("指摘が見つかりません。")
                continue
            item = SendbackFinding.of(finding)
            if not can_regenerate(item):
                if strict:
                    raise SendbackFindingInvalidError(
                        "再生成に渡せるのは、採用した設計・不整合の指摘だけです。"
                    )
                continue
            findings.append(item)
        return findings

    async def rehearing(
        self, project: Project, finding_ids: Sequence[uuid.UUID]
    ) -> HearingPointsRead:
        """採用したヒアリングの指摘の観点を未決に戻し、指摘をまとめた問いかけをチャットに出す
        (app/review/rehearing.py)。プロジェクトに無い指摘は404、聞き直しに使えない指摘(採用して
        いない・ヒアリングが原因でない・観点のキーが無い)・指摘が無いは409、生成中は409。"""
        findings: list[SendbackFinding] = []
        for finding_id in dict.fromkeys(finding_ids):
            finding = await self._runs.get_finding(project_id=project.id, finding_id=finding_id)
            if finding is None:
                raise ReviewFindingNotFoundError("指摘が見つかりません。")
            item = SendbackFinding.of(finding)
            if not can_rehear(item):
                raise RehearingFindingInvalidError(
                    "聞き直せるのは、採用したヒアリングの指摘(観点のあるもの)だけです。"
                )
            findings.append(item)
        points = rehearing_points(findings)
        if not points:
            raise RehearingFindingInvalidError("聞き直す指摘を選んでください。")
        return await ChatService(self._session).reopen_points(
            project, list(points), rehearing_message(points)
        )

    async def followup_of(
        self, project: Project, finding: ReviewFinding
    ) -> FollowupState | None:
        """指摘1件の追跡の状態(採用していなければ None)。"""
        if finding.decision != "adopted":
            return None
        run = await self._runs.find_one(id=finding.run_id)
        if run is None:
            return None
        return (await self.followups(project, [run])).get(finding.id)

    async def current_fix_checks(
        self, project: Project, runs: Iterable[ReviewRun]
    ) -> dict[uuid.UUID, ReviewFixCheck]:
        """採用した指摘の最新の「直ったか」の判定のうち、今の版について判定したもの(版が変わった
        後の古い判定は出さない)。"""
        adopted = [
            (run, f) for run in runs for f in run.findings if f.decision == "adopted"
        ]
        latest = await self._fix_checks.latest_for([f.id for _, f in adopted])
        if not latest:
            return {}
        current = await self.current_versions(project)
        result: dict[uuid.UUID, ReviewFixCheck] = {}
        for run, f in adopted:
            check = latest.get(f.id)
            keys = version_keys(f.documents, run.targets)
            if check is not None and check.targets == {k: current.get(k) for k in keys}:
                result[f.id] = check
        return result

    async def current_versions(self, project: Project) -> dict[str, int | None]:
        """今の版 {doc_type: 表示中の版, "stage:N": 段階の内容の版}(段階は承認していない作り直しも
        変わったものに数える)。"""
        versions: dict[str, int | None] = {}
        for doc_type in DOC_TYPES_BY_MODE[project.mode]:
            current = await self._documents.get_current(project_id=project.id, doc_type=doc_type)
            versions[doc_type] = current.version if current is not None else None
        if project.mode == "detailed":
            for row in await self._stages.list_for_project(project.id):
                versions[stage_key(row.stage)] = row.version
        return versions

    async def row_diffs(
        self, project: Project, run: ReviewRun, current: Mapping[str, int | None]
    ) -> dict[str, RowDiff | None]:
        """版の変わったキーの、レビューしたときと今の行。比べられないものは None。"""
        changed = [k for k, v in run.targets.items() if current.get(k) != v]
        diffs: dict[str, RowDiff | None] = {}
        stored = run.snapshots or {}
        snapshots: dict[int, dict] = {}
        for key in changed:
            if key in _DOCUMENT_TYPES:
                diffs[key] = await self._document_diff(project, key, run.targets[key])
            elif stored.get(key) is not None:
                snapshots[int(key.split(":", 1)[1])] = stored[key]
        if snapshots:
            elements = await DesignStageService(self._session).elements_since(project, snapshots)
            for stage, (before, after) in elements.items():
                diffs[stage_key(stage)] = RowDiff(
                    element_rows(before, stage), element_rows(after, stage)
                )
        return diffs

    async def _document_diff(self, project: Project, doc_type: str, version: int) -> RowDiff | None:
        reviewed = await self._documents.get_version(
            project_id=project.id, doc_type=doc_type, version=version
        )
        current = await self._documents.get_current(project_id=project.id, doc_type=doc_type)
        if reviewed is None or current is None:
            return None  # レビューした版が直近の版の外に消えた
        before = document_model_rows(doc_type, reviewed.model)
        after = document_model_rows(doc_type, current.model)
        if before is None or after is None:
            return None
        return RowDiff(before, after)

    async def _stage_snapshots(self, project: Project, gate: ReviewGate) -> dict[str, dict] | None:
        """段階の区切りでレビューする段階の内容の写し(承認した写し)。文書の区切りは None。"""
        stages = GATE_STAGES[gate]
        if not stages:
            return None
        snapshots: dict[str, dict] = {}
        for row in await self._stages.list_for_project(project.id):
            if row.stage in stages and row.approved_model is not None:
                snapshots[stage_key(row.stage)] = row.approved_model
        return snapshots

    async def targets_for(self, project: Project, gate: ReviewGate) -> dict[str, int] | None:
        """区切りでレビューする今の版。そろっていない・モードに無い区切りは None。"""
        return (await self._all_targets(project, (gate,)))[gate]

    async def _all_targets(
        self, project: Project, gates: tuple[ReviewGate, ...]
    ) -> dict[ReviewGate, dict[str, int] | None]:
        documents = await self.document_targets(project)
        allowed = gates_for_mode(project.mode)
        result: dict[ReviewGate, dict[str, int] | None] = {}
        approved: dict[int, int | None] | None = None
        for gate in gates:
            if gate not in allowed or documents is None:
                result[gate] = None
            elif gate == DOCUMENTS_GATE:
                result[gate] = documents
            else:
                if approved is None:
                    approved = await self._approved_versions(project)
                result[gate] = stage_targets(documents, approved, GATE_STAGES[gate])
        return result

    async def _approved_versions(self, project: Project) -> dict[int, int | None]:
        """承認済み(古くない)段階の承認した版(段階の画面の状態と同じ規則)。"""
        views, _ = await DesignStageService(self._session).overview(project)
        return {
            row.stage: row.approved_version
            for row in await self._stages.list_for_project(project.id)
            if row.stage in views and views[row.stage].state == "approved"
        }

    async def _add_stage_documents(
        self, project: Project, gate: str, documents: dict[str, str]
    ) -> list[tuple[str, DocumentIssue]]:
        """段階の区切りの入力: 詳細設計書(区切りの段階の章)と、段階7の後は実装計画書の md を
        `documents`に足し、段階の検証の指摘(既知)を返す。"""
        stages = GATE_STAGES[cast(ReviewGate, gate)]
        _, sources = await DesignStageService(self._session).overview(project)
        collected = await DetailedDesignExportService(self._session).collect(
            project, render=False
        )
        chapters = [chapter for chapter in CHAPTERS if chapter.stage in stages]
        documents[DETAILED_DESIGN_DOC] = to_markdown(collected.source, chapters)
        if PLAN_STAGE in stages:
            documents[DETAILED_PLAN_DOC] = to_plan_markdown(collected.source)
        return stage_known_issues(
            {stage: validate_stage(stage, sources.stages.get(stage), sources) for stage in stages}
        )

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
