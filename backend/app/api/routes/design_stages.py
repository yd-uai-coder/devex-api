from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, status

from app.api.deps import CurrentProjectDep, SessionDep
from app.schemas.design_stage import (
    DesignStageApprove,
    DesignStageGenerate,
    DesignStageRead,
    DesignStageSave,
)
from app.services.design_stage_generation_service import (
    DesignStageGenerationService,
    run_design_stage_generation,
)
from app.services.design_stage_service import DesignStageService

# UMLと同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/design-stages", tags=["design-stages"])

StageNumber = Annotated[int, Path(ge=1, le=7, description="段階番号(1〜7)")]


@router.get("", response_model=list[DesignStageRead])
async def list_design_stages(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[DesignStageRead]:
    """詳細設計モードの段階1〜7の状態を取得する(未着手の段階も含む)。画面は下書きの生成の完了を
    この一覧のポーリングで待つため、止まった生成(15分超)はここで回収してから返す。"""
    await DesignStageGenerationService(session).recover_stale(current_project.id)
    return await DesignStageService(session).list_stages(current_project)


@router.post(
    "/{stage}/generate", response_model=DesignStageRead, status_code=status.HTTP_202_ACCEPTED
)
async def generate_design_stage(
    stage: StageNumber,
    session: SessionDep,
    current_project: CurrentProjectDep,
    background_tasks: BackgroundTasks,
    payload: DesignStageGenerate | None = None,
) -> DesignStageRead:
    """段階のAIの下書きの生成を受け付け、バックグラウンドで実行する(Phase 20 の時点で段階1〜5)。
    段階は「生成中」になり、終わると`completed`/`failed`になる。background taskには値だけを渡す
    (doc生成・UML図の生成と同じ理由)。段階5は、本文の`function_ids`で下書きを作る処理を選べる。"""
    function_ids = payload.function_ids if payload is not None else None
    accepted = await DesignStageGenerationService(session).request_generation(
        current_project, stage=stage, function_ids=function_ids
    )
    background_tasks.add_task(
        run_design_stage_generation,
        current_project.id,
        current_project.user_id,
        stage,
        function_ids,
    )
    return accepted


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
