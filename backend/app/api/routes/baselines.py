from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import CurrentProjectDep, CurrentUserDep, SessionDep
from app.models.baseline import Baseline
from app.models.project import Project
from app.schemas.baseline import (
    BaselineCreate,
    BaselineFreshnessRead,
    BaselineOriginRead,
    BaselineRead,
    BaselineRequirementRead,
    BaselineSummaryRead,
)
from app.services.baseline_service import BaselineService

# 段階・レビューと同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/baselines", tags=["baselines"])

BaselineNumber = Annotated[int, Path(ge=1, description="合意済みの版の番号(B-1 なら 1)")]


async def _read(service: BaselineService, project: Project, baseline: Baseline) -> BaselineRead:
    included = await service.included_change_requests([baseline])
    freshness = await service.freshness_of(project, [baseline])
    return (
        BaselineRead.model_validate(baseline)
        .with_included(included.get(baseline.id, []))
        .with_fresh(freshness[baseline.id].fresh)
    )


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
    service = BaselineService(session)
    baseline = await service.create(current_project, payload, recorded_by=current_user.id)
    return await _read(service, current_project, baseline)


@router.get("", response_model=list[BaselineSummaryRead])
async def list_baselines(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[BaselineSummaryRead]:
    """プロジェクトの合意済みの版を新しい順に返す(写しは要約だけ。入れた CR と、新しいかを
    添える)。"""
    service = BaselineService(session)
    baselines = await service.list_for_project(current_project)
    included = await service.included_change_requests(baselines)
    freshness = await service.freshness_of(current_project, baselines)
    return [
        BaselineSummaryRead.model_validate(b)
        .with_included(included.get(b.id, []))
        .with_fresh(freshness[b.id].fresh)
        for b in baselines
    ]


@router.get("/{number}", response_model=BaselineRead)
async def get_baseline(
    number: BaselineNumber, session: SessionDep, current_project: CurrentProjectDep
) -> BaselineRead:
    """合意済みの版1つ(写しの中身付き。入れた CR と、新しいかを添える)。無ければ404。"""
    service = BaselineService(session)
    return await _read(service, current_project, await service.get(current_project, number))


@router.get("/{number}/freshness", response_model=BaselineFreshnessRead)
async def get_baseline_freshness(
    number: BaselineNumber, session: SessionDep, current_project: CurrentProjectDep
) -> BaselineFreshnessRead:
    """合意済みの版1つの鮮度(後の束・束の後に反映した CR・束を作った時点で古かった段階)。
    無ければ404。"""
    return BaselineFreshnessRead.of(
        await BaselineService(session).freshness(current_project, number)
    )


@router.get("/{number}/requirements", response_model=list[BaselineRequirementRead])
async def list_baseline_requirements(
    number: BaselineNumber, session: SessionDep, current_project: CurrentProjectDep
) -> list[BaselineRequirementRead]:
    """合意済みの版1つの要件定義書の写しにある要件の行(CR の対象を選ぶ画面用。写しの全体より
    軽い)。無ければ404。"""
    rows = await BaselineService(session).requirements(current_project, number)
    return [BaselineRequirementRead.model_validate(row) for row in rows]


@router.get("/{number}/origins", response_model=list[BaselineOriginRead])
async def list_baseline_origins(
    number: BaselineNumber, session: SessionDep, current_project: CurrentProjectDep
) -> list[BaselineOriginRead]:
    """合意済みの版1つで、新しい要件の予測の起点に選べる要素(要件・画面・API・処理。CR の起点を選ぶ
    画面用)。無ければ404。"""
    origins = await BaselineService(session).origins(current_project, number)
    return [BaselineOriginRead(key=o.key, kind=o.kind, label=o.label) for o in origins]
