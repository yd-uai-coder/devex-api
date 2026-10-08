import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project, function_list_model

from app.api.routes import api_router
from app.api.routes.design_stages import (
    approve_design_stage,
    list_design_stages,
    save_design_stage,
)
from app.models import DesignStage
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.design_stage import DesignStageApprove, DesignStageSave, StageIssueRead
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    DesignStageGenerationInProgressError,
    DesignStageInvalidError,
    DesignStageLockedError,
    DesignStageNotApprovableError,
    DesignStageNotFoundError,
    DesignStagesNotAvailableError,
    DesignStageVersionConflictError,
)

MODEL = function_list_model()


async def test_routes_save_approve_and_list_stage1(db_session: AsyncSession) -> None:
    """統合スモーク: ルート → サービス → リポジトリ → 純粋関数を1回ずつ通す。"""
    project = await create_detailed_project(db_session)

    saved = await save_design_stage(
        1, DesignStageSave(version=None, model=MODEL), db_session, project
    )
    approved = await approve_design_stage(
        1, DesignStageApprove(version=saved.version or 0), db_session, project
    )
    stages = await list_design_stages(db_session, project)

    assert saved.state == "reviewing"
    assert approved.state == "approved"
    assert approved.approved_version == 1
    assert [s.stage for s in stages] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert stages[1].is_open is True


async def test_simple_mode_project_has_only_stage8(db_session: AsyncSession) -> None:
    """簡易モードは段階8だけを持ち(入力は4文書)、他の段階は断る。"""
    project = await create_detailed_project(db_session, mode="simple")
    service = DesignStageService(db_session)

    stages = await service.list_stages(project)

    assert [(s.stage, s.mode, s.is_open) for s in stages] == [(8, "simple", False)]
    assert stages[0].missing_inputs == ["doc:internal_design", "doc:implementation_plan"]
    with pytest.raises(DesignStagesNotAvailableError):
        await service.save(project, stage=1, expected_version=None, model=MODEL)


async def test_save_locked_stage_is_rejected(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)

    with pytest.raises(DesignStageLockedError):
        await DesignStageService(db_session).save(
            project, stage=2, expected_version=None, model=MODEL
        )


async def test_save_with_stale_version_is_a_conflict(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=1, expected_version=None, model=MODEL)

    with pytest.raises(DesignStageVersionConflictError):
        await service.save(project, stage=1, expected_version=None, model=MODEL)


async def test_saving_approved_stage_returns_it_to_review(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=1, expected_version=None, model=MODEL)
    await service.approve(project, stage=1, expected_version=1)

    edited = await service.save(project, stage=1, expected_version=1, model={"functions": []})

    assert edited.state == "reviewing"
    assert edited.version == 2
    assert edited.approved_version == 1


async def test_approve_records_input_fingerprint(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=1, expected_version=None, model=MODEL)

    await service.approve(project, stage=1, expected_version=1)

    row = await DesignStageRepository(db_session).get(project_id=project.id, stage=1)
    assert isinstance(row, DesignStage)
    assert row.input_fingerprint == {"doc:external_design": 1}


async def test_regenerated_document_makes_stage_outdated_and_reapprovable(
    db_session: AsyncSession,
) -> None:
    project = await create_detailed_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=1, expected_version=None, model=MODEL)
    await service.approve(project, stage=1, expected_version=1)
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="external_design", content="# 改訂"
    )
    await db_session.commit()

    [stage1, *_] = await service.list_stages(project)
    reapproved = await service.approve(project, stage=1, expected_version=1)

    assert stage1.state == "outdated"
    assert reapproved.state == "approved"
    assert reapproved.version == 1


async def test_approving_already_approved_stage_is_rejected(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=1, expected_version=None, model=MODEL)
    await service.approve(project, stage=1, expected_version=1)

    with pytest.raises(DesignStageNotApprovableError):
        await service.approve(project, stage=1, expected_version=1)


async def test_approving_empty_model_is_rejected(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=1, expected_version=None, model={})

    with pytest.raises(DesignStageNotApprovableError):
        await service.approve(project, stage=1, expected_version=1)


async def test_approving_not_started_stage_is_not_found(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)

    with pytest.raises(DesignStageNotFoundError):
        await DesignStageService(db_session).approve(project, stage=1, expected_version=1)


def test_design_stage_routes_are_registered() -> None:
    """api_router(app/api/routes/__init__.py)に段階のルーターを登録したこと。"""
    app = FastAPI()
    app.include_router(api_router)
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/design-stages" in paths
    assert "/projects/{project_id}/design-stages/{stage}/approve" in paths


async def test_approve_rejects_stage_with_validation_errors(db_session: AsyncSession) -> None:
    """段階ごとの検証: エラーがあれば承認できず、読み取りに指摘が載る。"""
    project = await create_detailed_project(db_session)
    service = DesignStageService(db_session)
    saved = await service.save(
        project, stage=1, expected_version=None, model=function_list_model(group="無い")
    )

    with pytest.raises(DesignStageInvalidError):
        await service.approve(project, stage=1, expected_version=1)
    assert [(i.severity, i.code) for i in saved.issues] == [
        ("error", "UNKNOWN_GROUP"),
        ("warning", "UNUSED_GROUP"),
    ]
    assert isinstance(saved.issues[0], StageIssueRead)


async def test_generating_stage_cannot_be_saved_or_approved(db_session: AsyncSession) -> None:
    """生成中の段階は、AIの結果で人の編集を上書きしないよう保存・承認を断る。"""
    project = await create_detailed_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=1, expected_version=None, model=MODEL)
    row = await DesignStageRepository(db_session).get(project_id=project.id, stage=1)
    assert row is not None
    row.generation_status = "generating"
    await db_session.commit()

    with pytest.raises(DesignStageGenerationInProgressError):
        await service.save(project, stage=1, expected_version=1, model=MODEL)
    with pytest.raises(DesignStageGenerationInProgressError):
        await service.approve(project, stage=1, expected_version=1)
    [stage1, *_] = await service.list_stages(project)
    assert stage1.generation_status == "generating"
