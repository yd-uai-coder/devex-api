"""段階8の単位の AI 向けの版(画面の「AI 向けにコピー」)の API のテスト。

SUT は`DesignStageService.unit_ai_markdown`とルート`get_unit_ai_markdown`、ドライバはこのテスト。
スタブ不要 ── DB はテスト用のインメモリ SQLite(`db_session`)を使い、組み立ては純粋関数の
テストで確かめたので、ここでは保存済みの手順書から作ること・警告の件数・断る条件だけを見る。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import (
    create_document_project,
    create_stage7_project,
    procedure_doc_model,
)

from app.api.routes.design_stages import get_unit_ai_markdown
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    DesignStageLockedError,
    DesignUnitNotFoundError,
    DesignUnitProcedureNotFoundError,
)


async def test_route_returns_ai_markdown_of_saved_procedure(db_session: AsyncSession) -> None:
    """統合スモーク: 保存済み(未承認)の手順書から作り、未承認と未定義の件数を返す。"""
    project = await create_document_project(db_session)
    await DesignStageService(db_session).save(
        project, stage=8, expected_version=None, model=procedure_doc_model()
    )

    result = await get_unit_ai_markdown("M-01-T02", db_session, project)

    assert (result.unit_id, result.state, result.finding_total, result.critical) == (
        "M-01-T02",
        "reviewing",
        1,
        1,
    )
    assert result.markdown.startswith("> ⚠ 段階8(実装手順書)は未承認です。")
    assert "### 段階5 F-01 予約を登録する" in result.markdown


async def test_approved_procedure_has_no_state_warning(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)
    service = DesignStageService(db_session)
    await service.save(project, stage=8, expected_version=None, model=procedure_doc_model())
    await service.approve(project, stage=8, expected_version=1)

    result = await service.unit_ai_markdown(project, " M-01-T02 ")

    assert result.state == "approved"
    assert result.unit_id == "M-01-T02"
    assert result.markdown.startswith("> ⚠ この単位には「未定義・要決定」が 1 件")


async def test_unit_without_procedure_is_not_found(db_session: AsyncSession) -> None:
    """段階7にある単位でも、手順書が無い(未生成・段階7と合わない)なら見つからない。"""
    project = await create_document_project(db_session)
    service = DesignStageService(db_session)

    with pytest.raises(DesignUnitProcedureNotFoundError):
        await service.unit_ai_markdown(project, "M-01-T02")
    await service.save(
        project, stage=8, expected_version=None, model=procedure_doc_model(title="古い名前")
    )
    with pytest.raises(DesignUnitProcedureNotFoundError):
        await service.unit_ai_markdown(project, "M-01-T02")


async def test_unknown_unit_and_locked_stage(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)
    with pytest.raises(DesignUnitNotFoundError):
        await DesignStageService(db_session).unit_ai_markdown(project, "M-09-T01")

    locked = await create_stage7_project(db_session)
    with pytest.raises(DesignStageLockedError):
        await DesignStageService(db_session).unit_ai_markdown(locked, "M-01-T02")
