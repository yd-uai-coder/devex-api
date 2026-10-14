import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.review import ReviewFinding, ReviewFixCheck, ReviewRun
from app.repositories.base import CRUDRepository
from app.review import ReviewFindingOut, sort_findings


class ReviewRunRepository(CRUDRepository[ReviewRun]):
    """設計レビューの回と指摘の永続化。指摘は回と一緒に読む(selectinload)。"""

    model = ReviewRun

    def _default_options(self) -> tuple[Any, ...]:
        return (selectinload(ReviewRun.findings),)

    async def create_run(
        self,
        *,
        project_id: uuid.UUID,
        gate: str,
        targets: Mapping[str, int],
        trigger: str,
        model: str,
        status: str = "running",
        snapshots: Mapping[str, dict] | None = None,
    ) -> ReviewRun:
        """回を作る(既定は実行中)。flushまで行う。始めた時刻はアプリで入れる ── 同じ秒に作った
        回どうしも新しい順に並べられるように(SQLiteの now() は秒の単位)。"""
        run = ReviewRun(
            project_id=project_id,
            gate=gate,
            targets=dict(targets),
            trigger=trigger,
            model=model,
            status=status,
            snapshots=dict(snapshots) if snapshots is not None else None,
            started_at=datetime.now(UTC),
            findings=[],
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_latest(self, *, project_id: uuid.UUID, gate: str) -> ReviewRun | None:
        """区切りの最新の回(指摘付き)。無ければ None。"""
        stmt = (
            select(ReviewRun)
            .where(ReviewRun.project_id == project_id, ReviewRun.gate == gate)
            .order_by(ReviewRun.started_at.desc())
            .limit(1)
            .options(*self._default_options())
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_with_adopted(self, *, project_id: uuid.UUID, gate: str) -> list[ReviewRun]:
        """区切りの回のうち、採用した指摘を持つもの(指摘付き、新しい順)。"""
        adopted = select(ReviewFinding.run_id).where(ReviewFinding.decision == "adopted")
        stmt = (
            select(ReviewRun)
            .where(
                ReviewRun.project_id == project_id,
                ReviewRun.gate == gate,
                ReviewRun.id.in_(adopted),
            )
            .order_by(ReviewRun.started_at.desc())
            .options(*self._default_options())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_finding(
        self, *, project_id: uuid.UUID, finding_id: uuid.UUID
    ) -> ReviewFinding | None:
        """プロジェクトの回の指摘1件。別のプロジェクトの指摘は None。"""
        stmt = (
            select(ReviewFinding)
            .join(ReviewRun, ReviewFinding.run_id == ReviewRun.id)
            .where(ReviewFinding.id == finding_id, ReviewRun.project_id == project_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_running(self, project_id: uuid.UUID) -> list[ReviewRun]:
        """プロジェクトの実行中の回。"""
        return await self.list_all(project_id=project_id, status="running")

    async def finish(
        self,
        run: ReviewRun,
        *,
        status: str,
        error_code: str | None = None,
        usage: Mapping[str, Any] | None = None,
        findings: Sequence[ReviewFindingOut] = (),
    ) -> ReviewRun:
        """回を終える(成功・失敗)。成功なら指摘を重要度の順に保存し、トークン数を記録する
        (`usage`は LangChain の usage_metadata の形)。flushまで行う。"""
        run.status = status
        run.error_code = error_code
        run.finished_at = datetime.now(UTC)
        if usage:
            details = usage.get("input_token_details") or {}
            run.input_tokens = usage.get("input_tokens")
            run.output_tokens = usage.get("output_tokens")
            run.cache_read_tokens = details.get("cache_read")
            run.cache_creation_tokens = details.get("cache_creation")
        run.findings = [
            ReviewFinding(
                position=position,
                level=f.level,
                cause=f.cause,
                targets=list(f.targets),
                documents=list(f.documents),
                point_key=f.point_key,
                problem=f.problem,
                evidence=f.evidence,
                reason=f.reason,
                suggestion=f.suggestion,
            )
            for position, f in enumerate(sort_findings(findings))
        ]
        await self._session.flush()
        return run


class ReviewFixCheckRepository(CRUDRepository[ReviewFixCheck]):
    """採用した指摘が直ったかの判定の永続化。"""

    model = ReviewFixCheck

    async def add(self, **values: Any) -> ReviewFixCheck:
        """判定を1件足す(flushまで)。作った時刻はアプリで入れる(同じ秒の判定を新しい順に並べる)。"""
        check = ReviewFixCheck(created_at=datetime.now(UTC), **values)
        self._session.add(check)
        await self._session.flush()
        return check

    async def latest_for(
        self, finding_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, ReviewFixCheck]:
        """指摘ごとの最新の判定(判定の無い指摘は入らない)。"""
        if not finding_ids:
            return {}
        stmt = (
            select(ReviewFixCheck)
            .where(ReviewFixCheck.finding_id.in_(finding_ids))
            .order_by(ReviewFixCheck.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return {check.finding_id: check for check in result.scalars().all()}
