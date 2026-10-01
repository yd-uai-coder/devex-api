from typing import Annotated

from fastapi import APIRouter, Path

from app.api.deps import CurrentProjectDep, SessionDep
from app.schemas.design_stage import DesignStageApprove, DesignStageRead, DesignStageSave
from app.services.design_stage_service import DesignStageService

# UMLと同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/design-stages", tags=["design-stages"])

StageNumber = Annotated[int, Path(ge=1, le=7, description="段階番号(1〜7)")]


@router.get("", response_model=list[DesignStageRead])
async def list_design_stages(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[DesignStageRead]:
    """詳細設計モードの段階1〜7の状態を取得する(未着手の段階も含む)。"""
    return await DesignStageService(session).list_stages(current_project)


@router.put("/{stage}", response_model=DesignStageRead)
async def save_design_stage(
    stage: StageNumber,
    payload: DesignStageSave,
    session: SessionDep,
    current_project: CurrentProjectDep,
) -> DesignStageRead:
    """段階の内容を保存する(楽観ロック。承認済みの段階はレビュー中に戻る)。"""
    return await DesignStageService(session).save(
        current_project, stage=stage, expected_version=payload.version, model=payload.model
    )


@router.post("/{stage}/approve", response_model=DesignStageRead)
async def approve_design_stage(
    stage: StageNumber,
    payload: DesignStageApprove,
    session: SessionDep,
    current_project: CurrentProjectDep,
) -> DesignStageRead:
    """段階を承認する(古い段階は、内容を変えずに承認し直せる)。"""
    return await DesignStageService(session).approve(
        current_project, stage=stage, expected_version=payload.version
    )
