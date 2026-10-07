"""段階8(実装手順書)の段階の仕組み(開く条件・保存・承認・検証の指摘)のテスト。

SUT は`DesignStageService`とルート、ドライバはこのテスト。DB はインメモリ SQLite(`db_session`)。
スタブ: 「手順書が無くても検証する」のテストだけ、段階8の検証を偽の検証(固定の指摘を返す関数)に
差し替える(`monkeypatch`)。フィクスチャの設計には不足が無く、本物の検証では指摘が0件になるため。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import (
    create_document_project,
    create_stage7_project,
    procedure_doc_model,
)

from app.api.routes.design_stages import list_design_stages, save_design_stage
from app.detailed_design import STAGE_VALIDATORS, StageIssue
from app.schemas.design_stage import DesignStageSave
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.design_stage_service import DesignStageService
from app.services.errors import DesignStageGenerationNotSupportedError, DesignStageInvalidError


async def test_routes_list_and_save_stage8(db_session: AsyncSession) -> None:
    """統合スモーク: 段階1〜7を承認すると段階8が開き、ルートから保存でき、指摘に重要度が載る。"""
    project = await create_document_project(db_session)

    stages = await list_design_stages(db_session, project)
    saved = await save_design_stage(
        8,
        DesignStageSave(version=None, model=procedure_doc_model(module="app/unknown.py")),
        db_session,
        project,
    )

    stage8 = stages[7]
    assert (stage8.stage, stage8.state, stage8.is_open, stage8.issues) == (
        8,
        "not_started",
        True,
        [],
    )
    issue = saved.issues[0]
    assert (issue.code, issue.level, issue.fix_stage, issue.unit) == (
        "UNKNOWN_FILE",
        "major",
        4,
        "M-01-T02",
    )


async def test_stage8_is_locked_until_stage7_is_approved(db_session: AsyncSession) -> None:
    project = await create_stage7_project(db_session)

    stages = await DesignStageService(db_session).list_stages(project)

    assert stages[7].is_open is False
    assert stages[7].missing_inputs == ["stage:7"]


async def test_stage8_is_validated_without_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """手順書の行がまだ無くても、開いている段階8には検証の指摘が載る。"""
    project = await create_document_project(db_session)
    finding = StageIssue("warning", "FAKE", "m", "F-01", "critical", 5, "M-01-T02")
    monkeypatch.setitem(STAGE_VALIDATORS, 8, lambda model, sources: [finding])

    stage8 = (await DesignStageService(db_session).list_stages(project))[7]

    assert stage8.version is None
    assert [(i.code, i.level, i.fix_stage, i.unit) for i in stage8.issues] == [
        ("FAKE", "critical", 5, "M-01-T02")
    ]


async def test_warnings_do_not_block_approval(db_session: AsyncSession) -> None:
    """設計の不足(警告)と AI の指摘は、承認を止めない。"""
    project = await create_document_project(db_session)
    service = DesignStageService(db_session)
    model = procedure_doc_model(module="app/unknown.py")
    await service.save(project, stage=8, expected_version=None, model=model)

    approved = await service.approve(project, stage=8, expected_version=1)

    assert approved.state == "approved"


async def test_mismatched_unit_blocks_approval(db_session: AsyncSession) -> None:
    """段階7と合わない手順書(並べ替え・改名の後)は、作り直すまで承認できない。"""
    project = await create_document_project(db_session)
    service = DesignStageService(db_session)
    model = procedure_doc_model(unit_id="M-01-T01")
    await service.save(project, stage=8, expected_version=None, model=model)

    with pytest.raises(DesignStageInvalidError):
        await service.approve(project, stage=8, expected_version=1)


async def test_stage8_generation_is_not_supported_yet(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)

    with pytest.raises(DesignStageGenerationNotSupportedError):
        await DesignStageGenerationService(db_session).request_generation(project, stage=8)
