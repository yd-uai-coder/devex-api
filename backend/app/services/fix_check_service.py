"""採用した指摘が直ったかの判定(シャドーモード)のユースケース。

docs/internal_design.md 3.3節「8. AI 設計レビュー + テスト設計」。追跡(app/review/followup.py)で
「変更あり」になった採用した指摘だけを、狭い判定のモデル(Claude Haiku)に渡して、直ったかを記録する。
判定は記録して画面に小さく出すだけで、採否や追跡の状態は変えない(精度を測ってから使い方を決める)。
文書の生成の後・段階の承認の後に、レビューがオンのアカウントだけで流す。失敗は記録して、生成・承認を
止めない。1回に判定する指摘の数と、1日の回数に上限を置く(費用を抑えるため)。

段階を続けて承認すると、判定がバックグラウンドで並行に走り、どれも「今の版でまだ判定していない」と
見る。同じ指摘を同じ版で二度判定しないよう、判定の前に (指摘, 版) の予約札を Redis に置き
(`SET NX EX`)、置けた処理だけが判定する。
"""

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.claude import get_judge_llm
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.infrastructure.redis import get_redis_client
from app.models.project import Project
from app.models.review import ReviewFinding, ReviewFixCheck, ReviewRun
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.project import ProjectRepository
from app.repositories.review import ReviewFixCheckRepository, ReviewRunRepository
from app.review import (
    FixCheckOut,
    RowDiff,
    SendbackFinding,
    build_fix_check_messages,
    changed_rows_text,
    followup_state,
    gates_for_mode,
    review_effective,
    target_rows_text,
    version_keys,
)
from app.review.fix_check import NOT_FOUND_TEXT
from app.services.errors import RateLimitExceededError
from app.services.llm_retry import invoke_structured_with_usage
from app.services.rate_limit import RateLimit, RateLimiter
from app.services.review_service import ReviewService, failure_code

logger = structlog.get_logger(__name__)

_STRUCTURED_METHOD = "json_schema"

# 予約札の期限(秒)。判定(Haiku)の時間より十分に長く、版が変わらないまま承認し直す間隔より短く
_CLAIM_SECONDS = 600


def claim_key(finding_id: uuid.UUID, versions: Mapping[str, int | None]) -> str:
    """(指摘, 判定する版) の予約札の鍵。版が変われば別の鍵になる(直した後の判定を止めない)。"""
    digest = hashlib.sha1(
        json.dumps(dict(versions), sort_keys=True).encode(), usedforsecurity=False
    ).hexdigest()[:16]
    return f"fix_check:{finding_id}:{digest}"


async def run_fix_checks(project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """`BackgroundTasks`から呼ぶ判定(セッション・Redis は自前で開く。生成と同じ理由)。"""
    redis = get_redis_client()
    try:
        async with AsyncSessionLocal() as session:
            project = await ProjectRepository(session).get_by_id(project_id, user_id=user_id)
            user = await session.get(User, user_id)
            if project is not None and user is not None:
                await FixCheckService(session, redis=redis).run_for(project, user)
    finally:
        await redis.aclose()


@dataclass(frozen=True)
class _Candidate:
    finding: ReviewFinding
    keys: list[str]
    versions: dict[str, int | None]
    diffs: Mapping[str, RowDiff | None]


class FixCheckService:
    """採用した指摘が直ったかの判定と記録。"""

    def __init__(self, session: AsyncSession, *, redis: Redis | None = None, llm=None) -> None:
        # redis: 1日の回数を数える。llm: テストで差し替える
        self._session = session
        self._redis = redis
        self._llm = llm
        self._runs = ReviewRunRepository(session)
        self._checks = ReviewFixCheckRepository(session)
        self._documents = GeneratedDocumentRepository(session)
        self._reviews = ReviewService(session)

    async def run_for(self, project: Project, user: User) -> list[ReviewFixCheck]:
        """「変更あり」の採用した指摘のうち、今の版でまだ判定していないものを判定して commit する。
        レビューがオフ・判定のモデルを使えない(キーが無い)ときは何もしない。"""
        if not review_effective(is_superuser=user.is_superuser, review_enabled=user.review_enabled):
            return []
        if not settings.E2E_FAKE_LLM and not settings.ANTHROPIC_API_KEY and self._llm is None:
            return []
        candidates = await self._candidates(project)
        checks: list[ReviewFixCheck] = []
        for candidate in candidates[: settings.JUDGE_MAX_PER_RUN]:
            key = claim_key(candidate.finding.id, candidate.versions)
            if not await self._claim(key):
                continue  # ほかの処理が判定中か判定済み
            if not await self._within_limit(user):
                await self._release(key)
                break
            check = await self._check(project, candidate)
            if check is None:
                await self._release(key)  # 渡す内容が無く判定しなかった
            else:
                checks.append(check)
        await self._session.commit()
        return checks

    async def _candidates(self, project: Project) -> list[_Candidate]:
        current = await self._reviews.current_versions(project)
        candidates: list[_Candidate] = []
        seen: set[uuid.UUID] = set()
        for gate in gates_for_mode(project.mode):
            runs = await self._runs.list_with_adopted(project_id=project.id, gate=gate)
            for run in runs:
                candidates += await self._run_candidates(project, run, current, seen)
        return candidates

    async def _run_candidates(
        self,
        project: Project,
        run: ReviewRun,
        current: Mapping[str, int | None],
        seen: set[uuid.UUID],
    ) -> list[_Candidate]:
        adopted = [f for f in run.findings if f.decision == "adopted" and f.id not in seen]
        if not adopted:
            return []
        diffs = await self._reviews.row_diffs(project, run, current)
        latest = await self._checks.latest_for([f.id for f in adopted])
        result: list[_Candidate] = []
        for f in adopted:
            seen.add(f.id)
            state = followup_state(
                documents=f.documents,
                targets=f.targets,
                reviewed=run.targets,
                current=current,
                diffs=diffs,
            )
            if state != "changed":
                continue
            related = version_keys(f.documents, run.targets)
            keys = [k for k in related if current.get(k) != run.targets[k]]
            versions = {k: current.get(k) for k in related}
            check = latest.get(f.id)
            if check is not None and check.targets == versions:
                continue  # 今の版で判定済み
            result.append(_Candidate(f, keys, versions, diffs))
        return result

    async def _check(self, project: Project, candidate: _Candidate) -> ReviewFixCheck | None:
        """1件を判定して記録する(flushまで)。渡す内容が無い(段階の行を結べない)ときは判定しない。"""
        finding = SendbackFinding.of(candidate.finding)
        before, after = await self._contents(project, finding, candidate.keys, candidate.diffs)
        if after is None:
            return None
        values = {
            "finding_id": candidate.finding.id,
            "targets": candidate.versions,
            "model": settings.JUDGE_MODEL,
        }
        try:
            llm = self._llm or get_judge_llm()
            output, usage = await invoke_structured_with_usage(
                llm,
                FixCheckOut,
                build_fix_check_messages(finding, before, after),
                method=_STRUCTURED_METHOD,
            )
        except Exception as exc:  # noqa: BLE001 -- 判定の失敗は記録して、生成・承認を止めない
            logger.warning(
                "fix_check_failed",
                project_id=str(project.id),
                error_type=type(exc).__name__,
            )
            return await self._checks.add(**values, error_code=failure_code(exc))
        return await self._checks.add(
            **values,
            verdict=output.verdict,
            reason=output.reason,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    async def _contents(
        self,
        project: Project,
        finding: SendbackFinding,
        keys: Sequence[str],
        diffs: Mapping[str, RowDiff | None],
    ) -> tuple[str, str | None]:
        """レビューしたときと今の内容。対象の行を結べたら、その行と、今の対象の行・足した行・変えた
        行。結べなければレビューの引用と、今の関連文書の本文(段階は本文を渡さない)。"""
        before: list[str] = []
        after: list[str] = []
        for key in keys:
            diff = diffs.get(key)
            if diff is None:
                continue
            old = target_rows_text(diff.before, finding.targets)
            if old != NOT_FOUND_TEXT:
                before.append(old)
                after.append(changed_rows_text(diff, finding.targets))
        if after:
            return "\n".join(before), "\n".join(after)
        bodies: list[str] = []
        for key in keys:
            current = await self._documents.get_current(project_id=project.id, doc_type=key)
            if current is not None:
                bodies.append(current.content)
        evidence = f"(レビューの引用){finding.evidence}"
        return evidence, "\n\n".join(bodies) if bodies else None

    async def _claim(self, key: str) -> bool:
        """予約札を置く(置けたら True)。Redis を渡さない呼び出しでは札を使わない。"""
        if self._redis is None:
            return True
        return bool(await self._redis.set(key, "1", nx=True, ex=_CLAIM_SECONDS))

    async def _release(self, key: str) -> None:
        if self._redis is not None:
            await self._redis.delete(key)

    async def _within_limit(self, user: User) -> bool:
        """superuser ごとの1日の判定の回数(Redis を渡さない呼び出しでは数えない)。"""
        if self._redis is None:
            return True
        per_day = RateLimit(window_seconds=86400, max_requests=settings.JUDGE_RATE_LIMIT_PER_DAY)
        limiter = RateLimiter(self._redis, resource="fix_check", limits=[per_day])
        try:
            await limiter.enforce(str(user.id))
        except RateLimitExceededError:
            return False
        return True

