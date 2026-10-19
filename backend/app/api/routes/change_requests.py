from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import CurrentProjectDep, CurrentUserDep, SessionDep
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestRead,
    ChangeRequestSummaryRead,
    ChangeRequestUpdate,
    ChangeRequestWithdraw,
)
from app.services.change_request_service import ChangeRequestService, ChangeRequestView

# 束と同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/change-requests", tags=["change-requests"])

ChangeRequestNumber = Annotated[int, Path(ge=1, description="CR の番号(CR-1 なら 1)")]


def _read(view: ChangeRequestView) -> ChangeRequestRead:
    return ChangeRequestRead.build(
        view.change_request,
        overlapping=view.overlapping,
        derived_from_number=view.derived_from_number,
        derived_numbers=view.derived_numbers,
        allowed_actions=view.allowed_actions,
    )


@router.post("", response_model=ChangeRequestRead, status_code=status.HTTP_201_CREATED)
async def create_change_request(
    payload: ChangeRequestCreate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """CR を下書きで作る。基準の束は番号の束(省けば最新)。簡易ドキュメントモードと束が無いは409、
    基準の束に無い要件は422。"""
    view = await ChangeRequestService(session).create(
        current_project, payload, created_by=current_user.id
    )
    return _read(view)


@router.get("", response_model=list[ChangeRequestSummaryRead])
async def list_change_requests(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[ChangeRequestSummaryRead]:
    """プロジェクトの CR を新しい順に返す(中身と時系列は詳細で)。"""
    change_requests = await ChangeRequestService(session).list_for_project(current_project)
    return [ChangeRequestSummaryRead.from_model(c) for c in change_requests]


@router.get("/{number}", response_model=ChangeRequestRead)
async def get_change_request(
    number: ChangeRequestNumber, session: SessionDep, current_project: CurrentProjectDep
) -> ChangeRequestRead:
    """CR 1つ(中身・合意の記録・時系列・重なり・写しの元と先)。無ければ404。"""
    return _read(await ChangeRequestService(session).get(current_project, number))


@router.patch("/{number}", response_model=ChangeRequestRead)
async def update_change_request(
    number: ChangeRequestNumber,
    payload: ChangeRequestUpdate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """CR の中身を直す(下書き・説明済みだけ。説明済みは下書きに戻る)。合意の後は409。"""
    view = await ChangeRequestService(session).update(
        current_project, number, payload, actor_id=current_user.id
    )
    return _read(view)


@router.post("/{number}/explain", response_model=ChangeRequestRead)
async def explain_change_request(
    number: ChangeRequestNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """下書きの CR を説明済みにする。下書きでなければ409。"""
    view = await ChangeRequestService(session).explain(
        current_project, number, actor_id=current_user.id
    )
    return _read(view)


@router.post("/{number}/decision", response_model=ChangeRequestRead)
async def decide_change_request(
    number: ChangeRequestNumber,
    payload: ChangeRequestDecision,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """説明済みの CR を合意か却下にし、合意の記録を残す。説明済みでなければ409。"""
    view = await ChangeRequestService(session).decide(
        current_project, number, payload, actor_id=current_user.id
    )
    return _read(view)


@router.post("/{number}/withdraw", response_model=ChangeRequestRead)
async def withdraw_change_request(
    number: ChangeRequestNumber,
    payload: ChangeRequestWithdraw,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """合意の前の CR を取り下げる。合意の後は409。"""
    view = await ChangeRequestService(session).withdraw(
        current_project, number, payload, actor_id=current_user.id
    )
    return _read(view)


@router.post(
    "/{number}/copy", response_model=ChangeRequestRead, status_code=status.HTTP_201_CREATED
)
async def copy_change_request(
    number: ChangeRequestNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> ChangeRequestRead:
    """却下した CR を写して、新しい番号の CR を下書きで作る。却下でなければ409。"""
    view = await ChangeRequestService(session).copy_rejected(
        current_project, number, created_by=current_user.id
    )
    return _read(view)
