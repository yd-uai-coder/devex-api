"""段階の作り直しに渡した指摘の記録(design_stages.generation_finding_ids)と、その読み出しのテスト。

SUT: DesignStageGenerationService.request_generation(指摘の ID を記録する)・DesignStageService
     (list_stages / read の generation_findings)・ReviewRunRepository.list_findings
ドライバ: 各テスト関数
スタブ: スタブ不要 ── 受け付けと読み出しだけを通し、LLM を呼ぶ execute は呼ばないため。
DB はインメモリの SQLite(記録した ID と、そこから引いた指摘そのものが検証対象のため)。
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project
from tests.fixtures.review import review_finding

from app.models.design_stage import DesignStage
from app.models.project import Project
from app.repositories.design_stage import DesignStageRepository
from app.repositories.review import ReviewRunRepository
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.design_stage_service import DesignStageService


async def _adopted(session: AsyncSession, project: Project, **kw: Any) -> uuid.UUID:
    """プロジェクトに、採用した指摘を1件保存する(レビューの回を経ずに直接)。"""
    runs = ReviewRunRepository(session)
    run = await runs.create_run(
        project_id=project.id, gate="stage6", targets={}, trigger="manual", model="m"
    )
    await runs.finish(run, status="succeeded", findings=[review_finding(**kw)])
    run.findings[0].decision = "adopted"
    await session.flush()
    return run.findings[0].id


async def _finish(session: AsyncSession, project: Project, stage: int) -> None:
    """生成中の印を外す(execute を呼ばずに次の受け付けを通すため)。"""
    row = await session.get(DesignStage, await _row_id(session, project, stage))
    assert row is not None
    row.generation_status = "completed"
    await session.commit()


async def _row_id(session: AsyncSession, project: Project, stage: int) -> uuid.UUID:
    for row in await DesignStageRepository(session).list_for_project(project.id):
        if row.stage == stage:
            return row.id
    raise AssertionError(f"stage {stage} has no row")


async def test_request_records_findings_and_reads_return_them(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    first = await _adopted(db_session, project, problem="予約の取消の処理が無い", level="critical")
    second = await _adopted(db_session, project, problem="一覧の並び順が無い", level="minor")

    read = await DesignStageGenerationService(db_session).request_generation(
        project, stage=1, finding_ids=[second, first]
    )

    # 渡した順に、重要度・対象・問題を返す
    assert [f.id for f in read.generation_findings] == [second, first]
    assert read.generation_findings[1].problem == "予約の取消の処理が無い"
    assert read.generation_findings[1].level == "critical"
    listed = await DesignStageService(db_session).list_stages(project)
    stage1 = next(s for s in listed if s.stage == 1)
    assert stage1.generation_findings == read.generation_findings
    assert all(s.generation_findings == [] for s in listed if s.stage != 1)


async def test_request_without_findings_clears_the_record(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    finding = await _adopted(db_session, project)
    service = DesignStageGenerationService(db_session)
    await service.request_generation(project, stage=1, finding_ids=[finding])
    await _finish(db_session, project, 1)

    read = await service.request_generation(project, stage=1)

    assert read.generation_findings == []
    row = await db_session.get(DesignStage, await _row_id(db_session, project, 1))
    assert row is not None and row.generation_finding_ids is None


async def test_vanished_and_foreign_findings_are_skipped(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    other = await create_detailed_project(db_session)
    kept = await _adopted(db_session, project)
    foreign = await _adopted(db_session, other)
    await DesignStageGenerationService(db_session).request_generation(
        project, stage=1, finding_ids=[kept]
    )
    row = await db_session.get(DesignStage, await _row_id(db_session, project, 1))
    assert row is not None
    # 記録の後に消えた指摘と、別のプロジェクトの指摘(読み出しでは入れない)
    row.generation_finding_ids = [str(uuid.uuid4()), str(foreign), str(kept)]
    await db_session.commit()

    read = await DesignStageService(db_session).read(project.id, 1)

    assert [f.id for f in read.generation_findings] == [kept]
    assert await ReviewRunRepository(db_session).list_findings(
        project_id=project.id, finding_ids=[foreign]
    ) == []
