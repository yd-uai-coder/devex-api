"""段階5のシーケンス図の API(保存した手順から導く SVG と、図にするときの指摘)のテスト。

SUT は`DesignStageService.procedure_sequence`とルート`get_procedure_sequence`、
ドライバはこのテスト。DB はインメモリ SQLite(`db_session`)。
スタブ不要 ── 図は保存した段階5・段階4の内容から純粋関数で導き、LLM も外部サービスも呼ばないため。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import (
    create_stage4_project,
    create_stage5_project,
    procedure_model,
)

from app.api.routes.design_stages import get_procedure_sequence
from app.models.project import Project
from app.services.design_stage_service import DesignStageService
from app.services.errors import DesignProcedureNotFoundError, DesignStageLockedError

ROUTE = "app/api/routes/reservations.py"


async def _save(session: AsyncSession, project: Project, model: dict) -> None:
    """段階5を保存する(承認しない。下書き・レビュー中の手順からも図を導けることを見る)。"""
    row = await DesignStageService(session).read(project.id, 5)
    await DesignStageService(session).save(
        project, stage=5, expected_version=row.version, model=model
    )


async def test_route_returns_svg_of_saved_procedure(db_session: AsyncSession) -> None:
    project = await create_stage5_project(db_session)
    await _save(db_session, project, procedure_model())

    sequence = await get_procedure_sequence("F-01", db_session, project)

    assert sequence.function_id == "F-01"
    assert sequence.svg.startswith("<svg ") and ROUTE in sequence.svg
    assert sequence.issues == []


async def test_sequence_reports_issues_of_saved_procedure(db_session: AsyncSession) -> None:
    model = procedure_model()
    model["procedures"][0]["steps"].append({"caller": ROUTE, "callee": "利用者", "data": "201"})
    project = await create_stage5_project(db_session)
    await _save(db_session, project, model)

    sequence = await DesignStageService(db_session).procedure_sequence(project, "F-01")

    assert [(i.step_id, i.code) for i in sequence.issues] == [("F-01#2", "RETURN_AS_CALL")]


async def test_sequence_of_unselected_function_is_not_found(db_session: AsyncSession) -> None:
    project = await create_stage5_project(db_session)
    await _save(db_session, project, procedure_model())

    with pytest.raises(DesignProcedureNotFoundError):
        await DesignStageService(db_session).procedure_sequence(project, "F-09")


async def test_sequence_requires_open_stage5(db_session: AsyncSession) -> None:
    project = await create_stage4_project(db_session)

    with pytest.raises(DesignStageLockedError):
        await DesignStageService(db_session).procedure_sequence(project, "F-01")
