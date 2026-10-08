from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Path, status
from fastapi.responses import Response

from app.api.deps import CurrentProjectDep, SessionDep
from app.api.responses import content_disposition
from app.schemas.design_stage import (
    DesignStageApprove,
    DesignStageGenerate,
    DesignStageRead,
    DesignStageSave,
    SequenceRead,
    UnitAiMarkdownRead,
    UnitContextRead,
)
from app.services.design_stage_generation_service import (
    DesignStageGenerationService,
    run_design_stage_generation,
)
from app.services.design_stage_service import DesignStageService
from app.services.detailed_design_export_service import BundleFile, DetailedDesignExportService

# UMLと同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/design-stages", tags=["design-stages"])

StageNumber = Annotated[int, Path(ge=1, le=8, description="段階番号(1〜8)")]


@router.get("", response_model=list[DesignStageRead])
async def list_design_stages(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[DesignStageRead]:
    """詳細設計モードの段階1〜8の状態を取得する(未着手の段階も含む)。画面は下書きの生成の完了を
    この一覧のポーリングで待つため、止まった生成(15分超)はここで回収してから返す。"""
    await DesignStageGenerationService(session).recover_stale(current_project.id)
    return await DesignStageService(session).list_stages(current_project)


@router.get("/document")
async def download_detailed_design(
    session: SessionDep, current_project: CurrentProjectDep
) -> Response:
    """詳細設計書(HTML・md)と載せた図(SVG・draw.io)、実装計画(HTML・md)を zip で
    ダウンロードする。段階1〜7がすべて承認済みでなければ409。zip に入れた図は`exported`になる。
    簡易ドキュメントモードのプロジェクトは409。"""
    return _zip_response(await DetailedDesignExportService(session).bundle(current_project))


@router.get("/procedure-document")
async def download_implementation_procedure(
    session: SessionDep, current_project: CurrentProjectDep
) -> Response:
    """実装手順書(`index.md`・単位ごとの md・AI 向けの版・HTML 1枚)を zip でダウンロードする。
    段階8が承認済みでなければ409。簡易ドキュメントモードのプロジェクトは409。"""
    return _zip_response(
        await DetailedDesignExportService(session).bundle_procedure(current_project)
    )


def _zip_response(bundle: BundleFile) -> Response:
    return Response(
        content=bundle.content,
        media_type=bundle.media_type,
        headers={"Content-Disposition": content_disposition(bundle.filename)},
    )


@router.get("/procedures/{function_id}/sequence", response_model=SequenceRead)
async def get_procedure_sequence(
    function_id: str, session: SessionDep, current_project: CurrentProjectDep
) -> SequenceRead:
    """段階5の処理1つのシーケンス図(保存した手順から導いた SVG と、図にするときの指摘)を返す。
    段階5が開いていなければ409、段階5で選んでいない処理は404。"""
    return await DesignStageService(session).procedure_sequence(current_project, function_id)


@router.get("/units/{unit_id}/context", response_model=UnitContextRead)
async def get_unit_context(
    unit_id: str, session: SessionDep, current_project: CurrentProjectDep
) -> UnitContextRead:
    """段階8の作業単位1つが参照する設計(段階5の手順・段階6の関数・段階4のモジュール)を展開して
    返す。段階7の 07 横断事項と開発環境も添える。段階8が開いていなければ409。"""
    return await DesignStageService(session).unit_context(current_project, unit_id)


@router.get("/units/{unit_id}/ai-markdown", response_model=UnitAiMarkdownRead)
async def get_unit_ai_markdown(
    unit_id: str, session: SessionDep, current_project: CurrentProjectDep
) -> UnitAiMarkdownRead:
    """段階8の作業単位1つの AI 向けの版(参照する設計を展開した md)を返す。保存済みの手順書から
    作り、段階8が承認済みでない・未定義が残るときは先頭で警告する。段階8が開いていなければ409、
    段階7に無い単位・手順書の無い単位は404。"""
    return await DesignStageService(session).unit_ai_markdown(current_project, unit_id)


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
    """段階のAIの下書きの生成を受け付け、バックグラウンドで実行する。
    段階は「生成中」になり、終わると`completed`/`failed`になる。background taskには値だけを渡す
    (doc生成・UML図の生成と同じ理由)。段階5は、本文の`function_ids`で下書きを作る処理を選べる。
    段階6は、本文の`logics`で下書きを作る関数を選べる。段階8は、本文の`unit_ids`で手順書を作る
    作業単位を選べる。"""
    function_ids = payload.function_ids if payload is not None else None
    unit_ids = payload.unit_ids if payload is not None else None
    logics = (
        [(t.module, t.function) for t in payload.logics]
        if payload is not None and payload.logics is not None
        else None
    )
    accepted = await DesignStageGenerationService(session).request_generation(
        current_project, stage=stage, function_ids=function_ids, logics=logics, unit_ids=unit_ids
    )
    background_tasks.add_task(
        run_design_stage_generation,
        current_project.id,
        current_project.user_id,
        stage,
        function_ids,
        logics,
        unit_ids,
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
