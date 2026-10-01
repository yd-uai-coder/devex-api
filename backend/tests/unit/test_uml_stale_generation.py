"""止まったUML図の生成の回収(気づき#5)のテスト。

SUT: UmlGenerationService.recover_stale と、それを呼ぶ受け付け・一覧のルート
ドライバ: 各テスト関数
スタブ不要 ── LLMを呼ばない(受け付けだけを行い、バックグラウンドの実行は走らせない)。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.uml import create_project_with_internal_design

from app.api.routes.uml import list_diagrams
from app.repositories.uml_diagram import UmlDiagramRepository
from app.repositories.uml_generation_run import UmlGenerationRunRepository
from app.services.uml_generation_service import UmlGenerationService
from app.uml.generation import STALE_MESSAGE

LATER = datetime.now(UTC) + timedelta(minutes=16)


async def test_recover_stale_fails_diagram_and_run(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    service = UmlGenerationService(db_session)
    run = await service.request_generation(project_id=project.id, notation="component", subjects=[])

    recovered = await service.recover_stale(project.id, now=LATER)

    assert recovered == 1
    [diagram] = await UmlDiagramRepository(db_session).list_all(project_id=project.id)
    assert diagram.generation_status == "failed"
    assert diagram.generation_error == STALE_MESSAGE
    runs = UmlGenerationRunRepository(db_session)
    saved_run = await runs.get_by_id(run.id, project_id=project.id)
    assert saved_run is not None
    assert saved_run.status == "failed"
    assert [r["reason_code"] for r in saved_run.results] == ["STALE_GENERATION"]


async def test_recent_generation_is_left_alone(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    service = UmlGenerationService(db_session)
    await service.request_generation(project_id=project.id, notation="component", subjects=[])

    assert await service.recover_stale(project.id) == 0
    assert await UmlDiagramRepository(db_session).has_generating(project.id) is True


async def test_list_diagrams_route_recovers_stale_generation(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    await UmlGenerationService(db_session).request_generation(
        project_id=project.id, notation="component", subjects=[]
    )
    [diagram] = await UmlDiagramRepository(db_session).list_all(project_id=project.id)
    diagram.updated_at = datetime.now(UTC) - timedelta(minutes=30)
    await db_session.commit()

    [listed] = await list_diagrams(db_session, project)

    assert listed.generation_status == "failed"
