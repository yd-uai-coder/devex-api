
import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.uml import (
    create_empty_diagram,
    create_project,
    create_project_with_internal_design,
)

from app.api.routes.uml import (
    compute_diagram_layout,
    generate_diagrams,
    get_diagram,
    list_diagrams,
    list_generation_candidates,
    list_generation_runs,
    update_diagram,
    validate_diagram,
)
from app.core.errors import BadRequestError
from app.schemas.uml_diagram import UmlDiagramUpdate
from app.schemas.uml_generation import UmlGenerateRequest, UmlSubjectSpec
from app.services.errors import UmlDiagramNotFoundError, UmlDiagramVersionConflictError
from app.services.uml_generation_service import run_uml_generation
from app.uml.domain import ComponentSemanticModel, DfdSemanticModel


async def test_get_diagram_raises_not_found_for_other_project(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    other_project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")

    with pytest.raises(UmlDiagramNotFoundError):
        await get_diagram(created.id, db_session, other_project)


async def test_update_diagram_increments_version(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    new_model = ComponentSemanticModel.model_validate(
        {"elements": [{"id": "c1", "name": "auth"}], "relations": []}
    )

    result = await update_diagram(
        created.id, UmlDiagramUpdate(version=1, semantic_model=new_model), db_session, project
    )

    assert result.version == 2
    assert len(result.semantic_model.elements) == 1


async def test_update_diagram_raises_conflict_on_stale_version(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    new_model = ComponentSemanticModel.model_validate({"elements": [], "relations": []})

    with pytest.raises(UmlDiagramVersionConflictError):
        await update_diagram(
            created.id, UmlDiagramUpdate(version=999, semantic_model=new_model), db_session, project
        )


async def test_update_diagram_raises_bad_request_on_notation_mismatch(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    dfd_model = DfdSemanticModel.model_validate({"elements": [], "relations": []})

    with pytest.raises(BadRequestError):
        await update_diagram(
            created.id, UmlDiagramUpdate(version=1, semantic_model=dfd_model), db_session, project
        )


async def test_validate_diagram_returns_valid_result_for_empty_model(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")

    result = await validate_diagram(created.id, db_session, project)

    assert result.is_valid


async def test_compute_diagram_layout_returns_layout_model(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    created = await create_empty_diagram(db_session, project.id, "component")
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [{"id": "c1", "name": "a"}, {"id": "c2", "name": "b"}],
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
        }
    )
    await update_diagram(
        created.id, UmlDiagramUpdate(version=1, semantic_model=model), db_session, project
    )

    result = await compute_diagram_layout(created.id, db_session, project)

    assert result.layout_model is not None
    assert set(result.layout_model.nodes) == {"c1", "c2"}


async def test_generate_diagrams_accepts_and_schedules_background_generation(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    background_tasks = BackgroundTasks()
    payload = UmlGenerateRequest(
        notation="dfd", subjects=[UmlSubjectSpec(subject="POST /api/v1/reservations")]
    )

    run = await generate_diagrams(payload, db_session, project, background_tasks)

    assert run.status == "running"
    assert run.results == []
    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is run_uml_generation
    assert task.args == (project.id, run.id)


async def test_list_diagrams_includes_generation_state(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    await generate_diagrams(
        UmlGenerateRequest(notation="component"), db_session, project, BackgroundTasks()
    )

    diagrams = await list_diagrams(db_session, project)

    assert [(d.notation, d.subject, d.generation_status) for d in diagrams] == [
        ("component", "", "generating")
    ]


async def test_list_generation_candidates_returns_dfd_subjects_and_er_tables(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)

    candidates = await list_generation_candidates(db_session, project)

    assert candidates.internal_design_version == 1
    assert candidates.dfd_subjects[0].code == "DF-1"
    assert candidates.er_tables == ["users", "reservations"]


async def test_list_generation_runs_returns_requested_history(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    await generate_diagrams(
        UmlGenerateRequest(notation="component"), db_session, project, BackgroundTasks()
    )

    runs = await list_generation_runs(db_session, project)

    assert len(runs) == 1
    assert runs[0].notation == "component"
    assert runs[0].status == "running"
