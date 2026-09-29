import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.services.data_item_service import DataItemService
from app.services.errors import (
    LayoutNodeLimitExceededError,
    LayoutValidationFailedError,
    UmlDiagramNotFoundError,
    UmlDiagramVersionConflictError,
)
from app.services.uml_diagram_service import UmlDiagramService
from app.uml.domain import ComponentSemanticModel, DfdSemanticModel, SemanticModelAdapter
from app.uml.validation.structural import MAX_ELEMENTS


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="備品予約システム")
    session.add(project)
    await session.flush()
    return project


async def test_create_returns_draft_with_empty_component_model(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = UmlDiagramService(db_session)

    diagram = await service.create(project_id=project.id, notation="component")

    assert diagram.status == "draft"
    assert diagram.version == 1
    assert diagram.view == "structure"
    assert diagram.semantic_model == {"notation": "component", "elements": [], "relations": []}


async def test_create_derives_view_from_notation_for_dfd(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = UmlDiagramService(db_session)

    diagram = await service.create(project_id=project.id, notation="dfd")

    assert diagram.view == "dataflow"


async def test_get_raises_not_found_for_other_project(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    other_project = await _create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await service.create(project_id=project.id, notation="component")

    with pytest.raises(UmlDiagramNotFoundError):
        await service.get(project_id=other_project.id, diagram_id=diagram.id)


async def test_update_persists_new_model_and_increments_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await service.create(project_id=project.id, notation="component")
    new_model = ComponentSemanticModel.model_validate(
        {"elements": [{"id": "c1", "name": "auth"}], "relations": []}
    )

    updated = await service.update(
        project_id=project.id,
        diagram_id=diagram.id,
        expected_version=1,
        semantic_model=new_model,
    )

    assert updated.version == 2
    assert updated.semantic_model["elements"] == [
        {"id": "c1", "name": "auth", "kind": "module", "description": None, "layer": None}
    ]


async def test_update_raises_conflict_on_stale_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await service.create(project_id=project.id, notation="component")
    new_model = ComponentSemanticModel.model_validate({"elements": [], "relations": []})

    with pytest.raises(UmlDiagramVersionConflictError):
        await service.update(
            project_id=project.id,
            diagram_id=diagram.id,
            expected_version=999,
            semantic_model=new_model,
        )


async def test_validate_returns_valid_result_for_empty_component_model(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await service.create(project_id=project.id, notation="component")

    result = await service.validate(project_id=project.id, diagram_id=diagram.id)

    assert result.is_valid


async def test_validate_uses_project_data_items_for_dfd(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    diagram_service = UmlDiagramService(db_session)
    data_item_service = DataItemService(db_session)
    data_item = await data_item_service.create(project_id=project.id, name="予約情報", fields=[])

    diagram = await diagram_service.create(project_id=project.id, notation="dfd")
    dfd_model = SemanticModelAdapter.validate_python(
        {
            "notation": "dfd",
            "elements": [
                {"id": "e1", "name": "利用者", "element_type": "external_entity"},
                {"id": "p1", "name": "予約を作成する", "element_type": "process"},
            ],
            "relations": [
                {
                    "id": "f1",
                    "source_id": "e1",
                    "target_id": "p1",
                    "data_item_id": str(data_item.id),
                }
            ],
        }
    )
    assert isinstance(dfd_model, DfdSemanticModel)
    await diagram_service.update(
        project_id=project.id,
        diagram_id=diagram.id,
        expected_version=1,
        semantic_model=dfd_model,
    )

    result = await diagram_service.validate(project_id=project.id, diagram_id=diagram.id)

    # p1は出力フローを持たないため PROCESS_MISSING_OUTPUT のエラーになるが、
    # data_item_id自体はプロジェクトのデータ辞書に実在するためUNKNOWN_DATA_ITEMにはならない。
    assert not any(issue.code == "UNKNOWN_DATA_ITEM" for issue in result.errors)
    assert any(issue.code == "PROCESS_MISSING_OUTPUT" for issue in result.errors)


async def test_compute_layout_persists_layout_model(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await service.create(project_id=project.id, notation="component")
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [
                {"id": "c1", "name": "認証API", "layer": "API層"},
                {"id": "c2", "name": "認証サービス", "layer": "Service層"},
            ],
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
        }
    )
    await service.update(
        project_id=project.id, diagram_id=diagram.id, expected_version=1, semantic_model=model
    )

    updated = await service.compute_layout(project_id=project.id, diagram_id=diagram.id)

    assert updated.layout_model is not None
    assert set(updated.layout_model["nodes"]) == {"c1", "c2"}
    assert set(updated.layout_model["edges"]) == {"r1"}
    assert updated.layout_model["metrics"]["crossings"] == 0


async def test_compute_layout_raises_when_node_limit_exceeded(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await service.create(project_id=project.id, notation="component")
    too_many = {
        "elements": [{"id": f"c{i}", "name": f"module{i}"} for i in range(MAX_ELEMENTS + 1)],
        "relations": [],
    }
    model = ComponentSemanticModel.model_validate(too_many)
    await service.update(
        project_id=project.id, diagram_id=diagram.id, expected_version=1, semantic_model=model
    )

    with pytest.raises(LayoutNodeLimitExceededError):
        await service.compute_layout(project_id=project.id, diagram_id=diagram.id)


async def test_compute_layout_raises_when_structural_validation_fails(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await service.create(project_id=project.id, notation="component")
    model = ComponentSemanticModel.model_validate(
        {
            "elements": [{"id": "c1", "name": "auth"}],
            # target_idが存在しない要素を参照している(参照切れ)
            "relations": [{"id": "r1", "source_id": "c1", "target_id": "missing"}],
        }
    )
    await service.update(
        project_id=project.id, diagram_id=diagram.id, expected_version=1, semantic_model=model
    )

    with pytest.raises(LayoutValidationFailedError):
        await service.compute_layout(project_id=project.id, diagram_id=diagram.id)
