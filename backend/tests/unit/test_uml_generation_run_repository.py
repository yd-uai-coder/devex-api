import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.uml import create_project

from app.repositories.uml_generation_run import UmlGenerationRunRepository


async def test_create_starts_run_in_running_state(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    repo = UmlGenerationRunRepository(db_session)
    requested = [{"subject": "", "diagram_id": str(uuid.uuid4())}]

    run = await repo.create(project_id=project.id, notation="component", requested=requested)

    assert run.status == "running"
    assert run.results == []
    assert run.requested == requested
    assert run.finished_at is None


async def test_get_by_id_is_scoped_to_project(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    other_project = await create_project(db_session)
    repo = UmlGenerationRunRepository(db_session)
    run = await repo.create(project_id=project.id, notation="er", requested=[])

    assert await repo.get_by_id(run.id, project_id=project.id) is not None
    assert await repo.get_by_id(run.id, project_id=other_project.id) is None


async def test_list_recent_limits_and_scopes_to_project(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    other_project = await create_project(db_session)
    repo = UmlGenerationRunRepository(db_session)
    for _ in range(3):
        await repo.create(project_id=project.id, notation="dfd", requested=[])
    await repo.create(project_id=other_project.id, notation="dfd", requested=[])

    assert len(await repo.list_recent(project.id)) == 3
    assert len(await repo.list_recent(project.id, limit=2)) == 2
