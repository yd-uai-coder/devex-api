"""簡易ドキュメントモードの段階8(実装手順書)の仕組みのテスト。

SUT は`DesignStageService`・`DesignStageGenerationService`(`generate_procedure_docs`)・
`DetailedDesignExportService.bundle_procedure`とルート、ドライバはこのテスト。DB はインメモリ
SQLite(`db_session`)。
スタブ: 手順書の生成は FakeLLM(tests/fixtures/fake_llm.py)── 構造化出力(Gemini)の代わり。
"""

import io
import zipfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.simple_procedure import (
    INTERNAL_DESIGN_MD,
    OLD_PLAN_MD,
    create_simple_procedure_project,
    simple_procedure_output,
)

from app.api.routes.design_stages import (
    download_implementation_procedure,
    get_unit_ai_markdown,
    get_unit_context,
    list_design_stages,
)
from app.detailed_design.procedure_doc_drafting import SimpleProcedureDocGenerationOutput
from app.repositories.generated_document import GeneratedDocumentRepository
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.design_stage_service import DesignStageService
from app.services.detailed_design_export_service import DetailedDesignExportService
from app.services.errors import DesignStagesNotAvailableError


async def test_simple_stage8_from_generation_to_zip(db_session: AsyncSession) -> None:
    """統合スモーク: 4文書から段階8が開き、単位を下書き → 承認 → 参照・AI 向けの版・zip。"""
    project = await create_simple_procedure_project(db_session)
    project_id = project.id

    [stage8] = await list_design_stages(db_session, project)
    assert (stage8.stage, stage8.mode, stage8.is_open, stage8.issues) == (8, "simple", True, [])
    assert stage8.plan is not None
    assert [m["name"] for m in stage8.plan["milestones"]] == ["予約の登録", "予約の一覧"]

    generation = DesignStageGenerationService(db_session)
    await generation.request_generation(project, stage=8, unit_ids=["M-01-T02"])
    llm = FakeLLM(structured=simple_procedure_output())
    await generation.execute(
        project_id=project_id, user_id=project.user_id, stage=8, unit_ids=["M-01-T02"], llm=llm
    )
    service = DesignStageService(db_session)
    drafted = await service.read(project_id, 8)
    assert llm.structured_output_calls == [SimpleProcedureDocGenerationOutput]
    assert drafted.model is not None
    [finding] = drafted.model["units"][0]["findings"]
    assert (finding["fix_stage"], finding["fix_document"]) == (8, "internal_design")

    approved = await service.approve(project, stage=8, expected_version=drafted.version or 0)
    context = await get_unit_context("M-01-T02", db_session, project)
    ai = await get_unit_ai_markdown("M-01-T02", db_session, project)
    response = await download_implementation_procedure(db_session, project)

    assert approved.state == "approved"
    assert [r.kind for r in context.refs] == ["dataflow", "module", "module"]
    assert "### 内部設計書 DF-1 POST /api/v1/reservations" in ai.markdown
    assert (ai.finding_total, ai.critical) == (1, 0)
    names = zipfile.ZipFile(io.BytesIO(bytes(response.body))).namelist()
    assert sorted(names) == [
        "M-01-T02_予約を登録する.md",
        "ai/M-01-T02.md",
        "implementation_procedure.html",
        "index.md",
    ]


async def test_other_stages_and_detailed_document_are_refused(db_session: AsyncSession) -> None:
    """簡易モードに段階1〜7は無い(保存・シーケンス図・詳細設計書の zip は 409)。"""
    project = await create_simple_procedure_project(db_session)
    service = DesignStageService(db_session)

    with pytest.raises(DesignStagesNotAvailableError):
        await service.save(project, stage=5, expected_version=None, model={})
    with pytest.raises(DesignStagesNotAvailableError):
        await service.procedure_sequence(project, "F-01")
    with pytest.raises(DesignStagesNotAvailableError):
        await DetailedDesignExportService(db_session).bundle(project)


async def test_regenerated_document_makes_stage8_outdated(db_session: AsyncSession) -> None:
    project = await create_simple_procedure_project(db_session)
    service = DesignStageService(db_session)
    saved = await service.save(project, stage=8, expected_version=None, model={"units": []})
    await service.approve(project, stage=8, expected_version=saved.version or 0)

    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="internal_design", content=INTERNAL_DESIGN_MD
    )
    await db_session.commit()
    [stage8] = await service.list_stages(project)

    assert stage8.state == "outdated"


async def test_old_documents_open_stage8_with_no_units(db_session: AsyncSession) -> None:
    """旧形式の WBS でも段階8は開き、単位0件と、再生成を促す最重要の指摘を返す。"""
    project = await create_simple_procedure_project(
        db_session, implementation_plan=OLD_PLAN_MD
    )

    [stage8] = await DesignStageService(db_session).list_stages(project)

    assert stage8.plan is not None and stage8.plan["milestones"] == []
    assert stage8.issues[0].code == "WBS_MISSING"
    assert stage8.issues[0].fix_document == "implementation_plan"
