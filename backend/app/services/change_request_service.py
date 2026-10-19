import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.change_requests import (
    ACTIVE_STATUSES,
    COPYABLE_STATUSES,
    DECISION_ACTIONS,
    INITIAL_STATUS,
    ChangeRequestAction,
    ChangeRequestStatus,
    Overlap,
    allowed_actions,
    next_status,
    normalize_targets,
    overlaps,
)
from app.models.baseline import Baseline
from app.models.change_request import ChangeRequest
from app.models.project import Project
from app.repositories.baseline import BaselineRepository
from app.repositories.change_request import ChangeRequestRepository
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestUpdate,
    ChangeRequestWithdraw,
    change_request_label,
)
from app.services.baseline_service import BaselineService
from app.services.errors import (
    ChangeRequestBaselineMissingError,
    ChangeRequestInvalidTransitionError,
    ChangeRequestNotFoundError,
    ChangeRequestTargetUnknownError,
    DesignStagesNotAvailableError,
)

logger = structlog.get_logger(__name__)

# 対象の要件を読む、束の写しの key
REQUIREMENTS_KEY = "requirements"


@dataclass(frozen=True)
class ChangeRequestView:
    """CR 1つと、詳細に出す周りの情報(重なり・写しの元と先・今できる操作)。"""

    change_request: ChangeRequest
    overlapping: list[Overlap]
    derived_from_number: int | None
    derived_numbers: list[int]
    allowed_actions: list[ChangeRequestAction]


class ChangeRequestService:
    """変更要求(CR)の作成・一覧・取得・更新と、状態の遷移(説明済み・合意 / 却下・取り下げ)・
    却下した CR の写し(docs/external_design.md 2.14節)。

    CR は合意済みの版(束)を基準にし、対象は基準の束の要件定義書にある要件 ID と、新しい要件を
    足す印。遷移の可否は app/change_requests/states.py の表で決め、遷移ごとに時系列へ1行残す。
    同じ要件を対象にする進行中の CR は止めず、詳細に重なりとして示す。詳細設計モードだけ。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._change_requests = ChangeRequestRepository(session)
        self._baselines = BaselineRepository(session)

    async def create(
        self, project: Project, payload: ChangeRequestCreate, *, created_by: uuid.UUID | None
    ) -> ChangeRequestView:
        """CR を下書きで作る。基準の束は番号の束(省けば最新の束)。束が1つも無ければ
        ChangeRequestBaselineMissingError、番号の束が無ければ BaselineNotFoundError。"""
        if project.mode != "detailed":
            raise DesignStagesNotAvailableError(
                f"Change requests are not available for project {project.id} "
                f"(mode={project.mode})"
            )
        baseline = await self._base_baseline(project, payload.baseline_number)
        targets = checked_targets(
            payload.target_requirement_ids, payload.adds_requirement, baseline
        )
        change_request = await self._change_requests.create(
            project_id=project.id,
            number=await self._change_requests.next_number(project.id),
            baseline_id=baseline.id,
            title=payload.title,
            content=payload.content,
            background_source=payload.background_source,
            background_date=payload.background_date,
            background_memo=payload.background_memo,
            target_requirement_ids=targets,
            adds_requirement=payload.adds_requirement,
            status=INITIAL_STATUS,
            action="create",
            created_by=created_by,
        )
        await self._session.commit()
        logger.info(
            "change_request_created", project_id=str(project.id), number=change_request.number
        )
        return await self._view(change_request)

    async def list_for_project(self, project: Project) -> list[ChangeRequest]:
        """プロジェクトの CR を新しい順に返す。"""
        return await self._change_requests.list_for_project(project.id)

    async def get(self, project: Project, number: int) -> ChangeRequestView:
        """番号の CR(詳細)。無ければ ChangeRequestNotFoundError。"""
        return await self._view(await self._load(project, number))

    async def update(
        self,
        project: Project,
        number: int,
        payload: ChangeRequestUpdate,
        *,
        actor_id: uuid.UUID | None,
    ) -> ChangeRequestView:
        """CR の中身を直す(送った項目だけ)。下書き・説明済みだけで、説明済みで直すと下書きに
        戻る(時系列に残す)。対象を変えたら、基準の束の要件定義書で確かめ直す。"""
        change_request = await self._load(project, number)
        to_status = self._next(change_request, "edit")
        changes = payload.model_dump(exclude_unset=True)
        if "target_requirement_ids" in changes or "adds_requirement" in changes:
            targets = changes.get("target_requirement_ids")
            adds = changes.get("adds_requirement")
            changes["target_requirement_ids"] = checked_targets(
                change_request.target_requirement_ids if targets is None else targets,
                change_request.adds_requirement if adds is None else adds,
                await self._baseline_with_items(change_request),
            )
        for field, value in changes.items():
            # 必須の項目に null を送ったときは、変えない(背景の日付だけは null で消せる)
            if value is None and field != "background_date":
                continue
            setattr(change_request, field, value)
        if to_status != change_request.status:
            await self._change_requests.add_event(
                change_request, action="edit", to_status=to_status, actor_id=actor_id
            )
        else:
            change_request.updated_at = datetime.now(UTC)
        await self._session.commit()
        return await self._view(change_request)

    async def explain(
        self, project: Project, number: int, *, actor_id: uuid.UUID | None
    ) -> ChangeRequestView:
        """下書きの CR を説明済みにする(クライアントに説明した)。"""
        change_request = await self._load(project, number)
        await self._change_requests.add_event(
            change_request,
            action="explain",
            to_status=self._next(change_request, "explain"),
            actor_id=actor_id,
        )
        await self._session.commit()
        return await self._view(change_request)

    async def decide(
        self,
        project: Project,
        number: int,
        payload: ChangeRequestDecision,
        *,
        actor_id: uuid.UUID | None,
    ) -> ChangeRequestView:
        """説明済みの CR を合意か却下にし、合意の記録(相手・日時・メモ・理由・記録した人)を残す。"""
        change_request = await self._load(project, number)
        action = DECISION_ACTIONS[payload.decision]
        to_status = self._next(change_request, action)
        change_request.decision_counterpart = payload.counterpart
        change_request.decided_at = payload.decided_at
        change_request.decision_memo = payload.memo
        change_request.decision_reason = payload.reason
        change_request.decision_recorded_by = actor_id
        await self._change_requests.add_event(
            change_request, action=action, to_status=to_status, actor_id=actor_id
        )
        await self._session.commit()
        logger.info(
            "change_request_decided",
            project_id=str(project.id),
            number=number,
            decision=payload.decision,
        )
        return await self._view(change_request)

    async def withdraw(
        self,
        project: Project,
        number: int,
        payload: ChangeRequestWithdraw,
        *,
        actor_id: uuid.UUID | None,
    ) -> ChangeRequestView:
        """合意の前(下書き・説明済み)の CR を取り下げる。理由は時系列に残す。"""
        change_request = await self._load(project, number)
        await self._change_requests.add_event(
            change_request,
            action="withdraw",
            to_status=self._next(change_request, "withdraw"),
            actor_id=actor_id,
            note=payload.reason.strip(),
        )
        await self._session.commit()
        return await self._view(change_request)

    async def copy_rejected(
        self, project: Project, number: int, *, created_by: uuid.UUID | None
    ) -> ChangeRequestView:
        """却下した CR を写して、新しい番号の CR を下書きで作る(中身・背景・対象・基準の束)。
        元の CR は却下と合意の記録のまま残し、時系列に写した先を1行足す。"""
        source = await self._load(project, number)
        if source.status not in COPYABLE_STATUSES:
            raise ChangeRequestInvalidTransitionError(
                f"CR-{number} cannot be copied (status={source.status})"
            )
        copied = await self._change_requests.create(
            project_id=project.id,
            number=await self._change_requests.next_number(project.id),
            baseline_id=source.baseline_id,
            title=source.title,
            content=source.content,
            background_source=source.background_source,
            background_date=source.background_date,
            background_memo=source.background_memo,
            target_requirement_ids=list(source.target_requirement_ids),
            adds_requirement=source.adds_requirement,
            status=INITIAL_STATUS,
            action="copy",
            derived_from_id=source.id,
            created_by=created_by,
            note=f"{change_request_label(source.number)}(却下)から写した",
        )
        await self._change_requests.add_event(
            source,
            action="copied",
            to_status=source.status,
            actor_id=created_by,
            note=f"{change_request_label(copied.number)} に写した",
        )
        await self._session.commit()
        logger.info(
            "change_request_copied",
            project_id=str(project.id),
            source=source.number,
            number=copied.number,
        )
        return await self._view(copied)

    async def _base_baseline(self, project: Project, number: int | None) -> Baseline:
        if number is not None:
            return await BaselineService(self._session).get(project, number)
        latest = await self._baselines.list_for_project(project.id)
        if not latest:
            raise ChangeRequestBaselineMissingError(f"No baseline in project {project.id}")
        return latest[0]

    async def _baseline_with_items(self, change_request: ChangeRequest) -> Baseline:
        """CR の基準の束を、写し付きで読む(CR と一緒に読んだ束は写しを読んでいないため)。"""
        baseline = await self._baselines.find_one(id=change_request.baseline_id)
        assert baseline is not None  # 束は消えない(CR はプロジェクトの削除でだけ一緒に消える)
        return baseline

    async def _load(self, project: Project, number: int) -> ChangeRequest:
        change_request = await self._change_requests.get_by_number(
            project_id=project.id, number=number
        )
        if change_request is None:
            raise ChangeRequestNotFoundError(f"Change request CR-{number} not found")
        return change_request

    def _next(self, change_request: ChangeRequest, action: ChangeRequestAction) -> str:
        to_status = next_status(change_request.status, action)
        if to_status is None:
            raise ChangeRequestInvalidTransitionError(
                f"CR-{change_request.number} cannot {action} (status={change_request.status})"
            )
        return to_status

    async def _view(self, change_request: ChangeRequest) -> ChangeRequestView:
        overlapping: list[Overlap] = []
        if change_request.status in ACTIVE_STATUSES:
            others = await self._change_requests.list_by_statuses(
                change_request.project_id, ACTIVE_STATUSES
            )
            overlapping = overlaps(
                change_request.target_requirement_ids,
                (
                    (other.number, other.target_requirement_ids)
                    for other in others
                    if other.id != change_request.id
                ),
            )
        derived_from_number = None
        if change_request.derived_from_id is not None:
            source = await self._change_requests.get_by_id(change_request.derived_from_id)
            derived_from_number = source.number if source is not None else None
        derived = await self._change_requests.find_derived(change_request.id)
        status: ChangeRequestStatus = change_request.status  # type: ignore[assignment]
        return ChangeRequestView(
            change_request=change_request,
            overlapping=overlapping,
            derived_from_number=derived_from_number,
            derived_numbers=[c.number for c in derived],
            allowed_actions=allowed_actions(status),
        )


def baseline_requirement_ids(baseline: Baseline) -> list[str]:
    """束の要件定義書の写しにある要件 ID(行の順)。写しが無い・model が無ければ空。"""
    item = next((i for i in baseline.items if i.key == REQUIREMENTS_KEY), None)
    if item is None or not item.model:
        return []
    return [str(row["id"]) for row in item.model.get("rows", []) if row.get("id")]


def checked_targets(
    requirement_ids: Iterable[str], adds_requirement: bool, baseline: Baseline
) -> list[str]:
    """対象の要件 ID を整え(空白・重複を除く)、基準の束の要件定義書にあるかを確かめる。無い ID が
    あるか、対象が空で新しい要件を足す印も無ければ ChangeRequestTargetUnknownError。"""
    targets = normalize_targets(requirement_ids)
    known = set(baseline_requirement_ids(baseline))
    unknown = [req_id for req_id in targets if req_id not in known]
    if unknown:
        raise ChangeRequestTargetUnknownError(
            f"基準の合意済みの版(B-{baseline.number})の要件定義書に無い要件です: "
            f"{', '.join(unknown)}"
        )
    if not targets and not adds_requirement:
        raise ChangeRequestTargetUnknownError(
            "対象の要件を選ぶか、新しい要件を足す CR にしてください。"
        )
    return targets
