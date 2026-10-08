"""段階8(実装手順書)の段階の仕組み(開く条件・保存・承認・検証の指摘・参照の展開・手順書の生成)の
テスト。

SUT は`DesignStageService`・`DesignStageGenerationService`(`generate_procedure_docs`)とルート、
ドライバはこのテスト。DB はインメモリ SQLite(`db_session`)。
スタブ:
- 「手順書が無くても検証する」のテストだけ、段階8の検証を偽の検証(固定の指摘を返す関数)に
  差し替える(`monkeypatch`)。フィクスチャの設計には不足が無く、本物の検証では指摘が0件になるため。
- 手順書の生成は FakeLLM(tests/fixtures/fake_llm.py)── 構造化出力(Gemini)の代わり。
  対象の単位ごとに1回呼ぶ。
"""

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import (
    create_document_project,
    create_stage7_project,
    procedure_doc_model,
    procedure_doc_output,
)
from tests.fixtures.fake_llm import FakeLLM

from app.api.routes.design_stages import (
    generate_design_stage,
    get_unit_context,
    list_design_stages,
    save_design_stage,
)
from app.detailed_design import STAGE_VALIDATORS, StageIssue
from app.detailed_design.procedure_doc_drafting import ProcedureDocGenerationOutput
from app.models.project import Project
from app.schemas.design_stage import DesignStageGenerate, DesignStageSave
from app.services import design_stage_generation_service as generation_service
from app.services.design_stage_generation_service import (
    STAGE_GENERATORS,
    DesignStageGenerationService,
    generate_procedure_docs,
)
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    DesignStageInvalidError,
    DesignStageLockedError,
    DesignUnitNotFoundError,
)


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


async def test_route_unit_context(db_session: AsyncSession) -> None:
    """単位の参照を、承認済みの段階1〜7から展開して返す(段階8の行が無くてもよい)。"""
    project = await create_document_project(db_session)

    context = await get_unit_context("M-01-T02", db_session, project)

    assert context.unit_id == "M-01-T02"
    assert [(r.kind, r.resolved, r.markdown is not None) for r in context.refs] == [
        ("procedure", True, True),
        ("logic", True, True),
        ("module", True, True),
    ]
    assert context.environment.endswith("Python 3.13 と PostgreSQL")


async def test_unit_context_requires_open_stage8(db_session: AsyncSession) -> None:
    project = await create_stage7_project(db_session)

    with pytest.raises(DesignStageLockedError):
        await DesignStageService(db_session).unit_context(project, "M-01-T02")


async def test_unit_context_of_unknown_unit(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)

    with pytest.raises(DesignUnitNotFoundError):
        await DesignStageService(db_session).unit_context(project, "M-09-T01")


async def _execute(
    session: AsyncSession, project: Project, llm: FakeLLM, unit_ids: list[str] | None = None
) -> None:
    await DesignStageGenerationService(session).execute(
        project_id=project.id, user_id=project.user_id, stage=8, unit_ids=unit_ids, llm=llm
    )


async def test_route_generates_selected_unit_and_can_be_approved(db_session: AsyncSession) -> None:
    """統合スモーク(段階8の生成): 単位を選んで受け付け → 手順書を下書き → そのまま承認できる。"""
    project = await create_document_project(db_session)
    project_id = project.id
    tasks = BackgroundTasks()
    llm = FakeLLM(structured=procedure_doc_output())

    accepted = await generate_design_stage(
        8, db_session, project, tasks, DesignStageGenerate(unit_ids=["M-01-T02"])
    )
    await _execute(db_session, project, llm, ["M-01-T02"])
    service = DesignStageService(db_session)
    stage8 = await service.read(project_id, 8)

    assert accepted.generation_status == "generating"
    assert tasks.tasks[0].args[5] == ["M-01-T02"]
    assert STAGE_GENERATORS[8] is generate_procedure_docs
    assert llm.structured_output_calls == [ProcedureDocGenerationOutput]
    assert stage8.state == "draft"
    assert stage8.model is not None
    [unit] = stage8.model["units"]
    assert (unit["unit_id"], unit["title"], unit["purpose"]) == (
        "M-01-T02",
        "予約を登録する",
        "予約を登録できる",
    )
    approved = await service.approve(project, stage=8, expected_version=stage8.version or 0)
    assert approved.state == "approved"


async def test_generation_defaults_to_undocumented_units(db_session: AsyncSession) -> None:
    """単位を選ばなければ、手順書の無い単位(段階7の並び順)を1つずつ下書きする。"""
    project = await create_document_project(db_session)
    project_id = project.id
    llm = FakeLLM(structured=procedure_doc_output())

    await DesignStageGenerationService(db_session).request_generation(project, stage=8)
    await _execute(db_session, project, llm)
    stage8 = await DesignStageService(db_session).read(project_id, 8)

    assert len(llm.structured_output_calls) == 2
    assert stage8.model is not None
    assert [u["unit_id"] for u in stage8.model["units"]] == ["M-01-T01", "M-01-T02"]


async def test_regenerates_only_requested_unit(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)
    project_id = project.id
    service = DesignStageService(db_session)
    model = procedure_doc_model()
    base = {"unit_id": "M-01-T01", "title": "開発環境を用意する", "purpose": "人"}
    model["units"].insert(0, base)
    await service.save(project, stage=8, expected_version=None, model=model)

    generation = DesignStageGenerationService(db_session)
    await generation.request_generation(project, stage=8, unit_ids=["M-01-T02"])
    llm = FakeLLM(structured=procedure_doc_output(purpose="作り直し"))
    await _execute(db_session, project, llm, ["M-01-T02"])
    stage8 = await service.read(project_id, 8)

    assert stage8.state == "regenerated"
    assert stage8.model is not None
    assert [(u["unit_id"], u["purpose"]) for u in stage8.model["units"]] == [
        ("M-01-T01", "人"),
        ("M-01-T02", "作り直し"),
    ]


async def test_generation_rejects_empty_unknown_too_many_and_other_stage(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_document_project(db_session)
    model = procedure_doc_model()
    model["units"].insert(0, {"unit_id": "M-01-T01", "title": "開発環境を用意する"})
    await DesignStageService(db_session).save(project, stage=8, expected_version=None, model=model)
    service = DesignStageGenerationService(db_session)

    with pytest.raises(DesignStageInvalidError, match="手順書を作る単位がありません"):
        await service.request_generation(project, stage=8)
    with pytest.raises(DesignStageInvalidError, match="段階7に無い単位です: M-09-T01"):
        await service.request_generation(project, stage=8, unit_ids=["M-09-T01"])
    monkeypatch.setattr(generation_service, "MAX_PROCEDURE_DOC_TARGETS", 1)
    with pytest.raises(DesignStageInvalidError, match="1 つまで"):
        await service.request_generation(project, stage=8, unit_ids=["M-01-T01", "M-01-T02"])
    with pytest.raises(DesignStageInvalidError, match="段階8だけ"):
        await service.request_generation(project, stage=7, unit_ids=["M-01-T01"])
    accepted = await service.request_generation(project, stage=8, unit_ids=["M-01-T01"])
    assert accepted.generation_status == "generating"
