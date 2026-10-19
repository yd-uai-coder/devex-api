from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import CurrentProjectDep, CurrentUserDep, SessionDep
from app.schemas.baseline import BaselineCreate, BaselineRead, BaselineSummaryRead
from app.services.baseline_service import BaselineService

# 段階・レビューと同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/baselines", tags=["baselines"])

BaselineNumber = Annotated[int, Path(ge=1, description="合意済みの版の番号(B-1 なら 1)")]


@router.post("", response_model=BaselineRead, status_code=status.HTTP_201_CREATED)
async def create_baseline(
    payload: BaselineCreate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> BaselineRead:
    """合意済みの版(束)を作る。今の文書・段階の承認の写しを写して凍結し、合意の記録を残す。
    渡した検証済みの CR を束に結ぶ。簡易ドキュメントモード・生成中・要件定義書か外部設計書が無い・
    検証済みでないかもう束に入っている CR は409、無い CR は404。"""
    baseline = await BaselineService(session).create(
        current_project, payload, recorded_by=current_user.id
    )
    return BaselineRead.model_validate(baseline)


@router.get("", response_model=list[BaselineSummaryRead])
async def list_baselines(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[BaselineSummaryRead]:
    """プロジェクトの合意済みの版を新しい順に返す(写しは要約だけ)。"""
    baselines = await BaselineService(session).list_for_project(current_project)
    return [BaselineSummaryRead.model_validate(baseline) for baseline in baselines]


@router.get("/{number}", response_model=BaselineRead)
async def get_baseline(
    number: BaselineNumber, session: SessionDep, current_project: CurrentProjectDep
) -> BaselineRead:
    """合意済みの版1つ(写しの中身付き)。無ければ404。"""
    return BaselineRead.model_validate(await BaselineService(session).get(current_project, number))
