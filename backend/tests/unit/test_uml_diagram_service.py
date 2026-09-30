import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.uml import create_empty_diagram, create_project

from app.services.data_item_service import DataItemService
from app.services.errors import (
    LayoutNodeLimitExceededError,
    LayoutValidationFailedError,
    UmlApprovalValidationFailedError,
    UmlDiagramNotApprovableError,
    UmlDiagramNotApprovedError,
    UmlDiagramNotFoundError,
    UmlDiagramVersionConflictError,
    UmlGenerationInProgressError,
    UmlLayoutRequiredError,
)
from app.services.uml_diagram_service import UmlDiagramService
from app.uml.domain import ComponentSemanticModel, DfdSemanticModel, SemanticModelAdapter
from app.uml.layout import LayoutModel
from app.uml.validation.structural import MAX_ELEMENTS


async def test_get_raises_not_found_for_other_project(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    other_project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")

    with pytest.raises(UmlDiagramNotFoundError):
        await service.get(project_id=other_project.id, diagram_id=diagram.id)


async def test_update_persists_new_model_and_increments_version(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")
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
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")
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
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")

    result = await service.validate(project_id=project.id, diagram_id=diagram.id)

    assert result.is_valid


async def test_validate_uses_project_data_items_for_dfd(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    diagram_service = UmlDiagramService(db_session)
    data_item_service = DataItemService(db_session)
    data_item = await data_item_service.create(project_id=project.id, name="予約情報", fields=[])

    diagram = await create_empty_diagram(db_session, project.id, "dfd")
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
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")
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
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")
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
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")
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


async def test_update_and_layout_are_rejected_while_generating(db_session: AsyncSession) -> None:
    """Phase 10: 生成中の図は生成結果で上書きされるため、更新・レイアウト実行を拒否する。"""
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")
    diagram.generation_status = "generating"
    await db_session.commit()
    model = ComponentSemanticModel.model_validate({"elements": [], "relations": []})

    with pytest.raises(UmlGenerationInProgressError):
        await service.update(
            project_id=project.id, diagram_id=diagram.id, expected_version=1, semantic_model=model
        )
    with pytest.raises(UmlGenerationInProgressError):
        await service.compute_layout(project_id=project.id, diagram_id=diagram.id)


async def test_validate_does_not_warn_for_data_item_used_by_another_dfd(
    db_session: AsyncSession,
) -> None:
    """Phase 10: 未参照データ項目の判定は、プロジェクト内の全DFDを横断する。"""
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    data_item_service = DataItemService(db_session)
    used_here = await data_item_service.create(
        project_id=project.id, name="予約リクエスト", fields=[]
    )
    used_elsewhere = await data_item_service.create(
        project_id=project.id, name="予約一覧", fields=[]
    )
    unused = await data_item_service.create(project_id=project.id, name="未使用", fields=[])

    def _dfd(item_id: uuid.UUID) -> DfdSemanticModel:
        model = SemanticModelAdapter.validate_python(
            {
                "notation": "dfd",
                "elements": [
                    {"id": "e1", "name": "利用者", "element_type": "external_entity"},
                    {"id": "p1", "name": "処理", "element_type": "process"},
                    {"id": "s1", "name": "reservations", "element_type": "data_store"},
                ],
                "relations": [
                    {
                        "id": "f1",
                        "source_id": "e1",
                        "target_id": "p1",
                        "data_item_id": str(item_id),
                    },
                    {
                        "id": "f2",
                        "source_id": "p1",
                        "target_id": "s1",
                        "data_item_id": str(item_id),
                    },
                ],
            }
        )
        assert isinstance(model, DfdSemanticModel)
        return model

    first = await create_empty_diagram(db_session, project.id, "dfd", subject="POST /a")
    second = await create_empty_diagram(db_session, project.id, "dfd", subject="GET /a")
    await service.update(
        project_id=project.id,
        diagram_id=first.id,
        expected_version=1,
        semantic_model=_dfd(used_here.id),
    )
    await service.update(
        project_id=project.id,
        diagram_id=second.id,
        expected_version=1,
        semantic_model=_dfd(used_elsewhere.id),
    )

    result = await service.validate(project_id=project.id, diagram_id=first.id)

    unreferenced = {w.element_id for w in result.warnings if w.code == "UNREFERENCED_DATA_ITEM"}
    assert unreferenced == {str(unused.id)}


async def test_update_saves_manual_layout_with_same_version(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")
    model = ComponentSemanticModel.model_validate(
        {"elements": [{"id": "c1", "name": "auth"}], "relations": []}
    )
    layout = LayoutModel.model_validate(
        {
            "width": 200,
            "height": 100,
            "nodes": {
                "c1": {"x": 40, "y": 20, "w": 100, "h": 40, "lane": 0, "row": 0},
                "deleted": {"x": 0, "y": 0, "w": 10, "h": 10, "lane": 0, "row": 1},
            },
            "edges": {},
            "metrics": {"crossings": 0, "overlaps": 0, "collisions": 0},
        }
    )

    updated = await service.update(
        project_id=project.id,
        diagram_id=diagram.id,
        expected_version=1,
        semantic_model=model,
        layout_model=layout,
    )

    assert updated.version == 2
    assert updated.layout_model is not None
    assert set(updated.layout_model["nodes"]) == {"c1"}
    assert updated.layout_model["nodes"]["c1"]["x"] == 40


async def test_update_without_layout_keeps_saved_layout_but_drops_deleted_elements(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")
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
    laid_out = await service.compute_layout(project_id=project.id, diagram_id=diagram.id)
    assert laid_out.layout_model is not None
    c1_before = laid_out.layout_model["nodes"]["c1"]
    only_c1 = ComponentSemanticModel.model_validate(
        {"elements": [{"id": "c1", "name": "認証API", "layer": "API層"}], "relations": []}
    )

    updated = await service.update(
        project_id=project.id, diagram_id=diagram.id, expected_version=2, semantic_model=only_c1
    )

    assert updated.layout_model is not None
    assert updated.layout_model["nodes"] == {"c1": c1_before}
    assert updated.layout_model["edges"] == {}


# ---- 状態遷移(M7)と承認
_TWO_MODULES = {
    "elements": [{"id": "c1", "name": "認証API"}, {"id": "c2", "name": "認証サービス"}],
    "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
}


async def _laid_out_diagram(db_session: AsyncSession, project_id: uuid.UUID):
    """2要素の図を保存し、自動レイアウトまで済ませる(承認できる状態、version=2)。"""
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project_id, "component")
    await service.update(
        project_id=project_id,
        diagram_id=diagram.id,
        expected_version=1,
        semantic_model=ComponentSemanticModel.model_validate(_TWO_MODULES),
    )
    return await service.compute_layout(project_id=project_id, diagram_id=diagram.id)


async def test_update_moves_draft_to_reviewing(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")

    updated = await service.update(
        project_id=project.id,
        diagram_id=diagram.id,
        expected_version=1,
        semantic_model=ComponentSemanticModel.model_validate(_TWO_MODULES),
    )

    assert updated.status == "reviewing"


async def test_approve_moves_to_approved_without_incrementing_version(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)

    approved = await service.approve(
        project_id=project.id, diagram_id=diagram.id, expected_version=2
    )

    assert approved.status == "approved"
    assert approved.version == 2


async def test_approve_is_allowed_from_draft(db_session: AsyncSession) -> None:
    """AIの出力を手直しせずに承認するケース(draft→approved)。"""
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    diagram.status = "draft"
    await db_session.commit()

    approved = await service.approve(
        project_id=project.id, diagram_id=diagram.id, expected_version=2
    )

    assert approved.status == "approved"


async def test_saving_an_approved_diagram_returns_it_to_reviewing(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=2)

    updated = await service.update(
        project_id=project.id,
        diagram_id=diagram.id,
        expected_version=2,
        semantic_model=ComponentSemanticModel.model_validate(_TWO_MODULES),
    )

    assert updated.status == "reviewing"


async def test_layout_of_an_approved_diagram_returns_it_to_reviewing(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=2)

    relaid = await service.compute_layout(project_id=project.id, diagram_id=diagram.id)

    assert relaid.status == "reviewing"


async def test_approve_rejects_stale_version(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)

    with pytest.raises(UmlDiagramVersionConflictError):
        await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=1)


async def test_approve_rejects_already_approved_diagram(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=2)

    with pytest.raises(UmlDiagramNotApprovableError):
        await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=2)


async def test_approve_requires_layout(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "component")
    await service.update(
        project_id=project.id,
        diagram_id=diagram.id,
        expected_version=1,
        semantic_model=ComponentSemanticModel.model_validate(_TWO_MODULES),
    )

    with pytest.raises(UmlLayoutRequiredError):
        await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=2)


async def test_approve_requires_layout_for_every_element(db_session: AsyncSession) -> None:
    """自動レイアウトの後に要素を追加し、座標を保存していない場合。"""
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    with_new_element = {
        **_TWO_MODULES,
        "elements": [*_TWO_MODULES["elements"], {"id": "c3", "name": "監査ログ"}],
    }
    await service.update(
        project_id=project.id,
        diagram_id=diagram.id,
        expected_version=2,
        semantic_model=ComponentSemanticModel.model_validate(with_new_element),
    )

    with pytest.raises(UmlLayoutRequiredError, match="c3"):
        await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=3)


async def test_approve_rejects_model_with_validation_errors(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    # 参照切れの関係を足す(自動レイアウトは検証エラーで拒否されるため、保存だけする)
    broken = {
        **_TWO_MODULES,
        "relations": [
            *_TWO_MODULES["relations"],
            {"id": "r2", "source_id": "c1", "target_id": "missing"},
        ],
    }
    await service.update(
        project_id=project.id,
        diagram_id=diagram.id,
        expected_version=2,
        semantic_model=ComponentSemanticModel.model_validate(broken),
    )

    with pytest.raises(UmlApprovalValidationFailedError):
        await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=3)


async def test_approve_is_rejected_while_generating(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    diagram.generation_status = "generating"
    await db_session.commit()

    with pytest.raises(UmlGenerationInProgressError):
        await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=2)


# ---- DFDの自動レイアウトはデータ項目名のラベル位置も保存する
async def test_compute_layout_places_data_item_labels_for_dfd(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    data_item = await DataItemService(db_session).create(
        project_id=project.id, name="予約リクエスト", fields=[]
    )
    diagram_service = UmlDiagramService(db_session)
    diagram = await create_empty_diagram(db_session, project.id, "dfd")
    dfd_model = SemanticModelAdapter.validate_python(
        {
            "notation": "dfd",
            "elements": [
                {"id": "e1", "name": "利用者", "element_type": "external_entity"},
                {"id": "p1", "name": "予約を作成する", "element_type": "process"},
                {"id": "s1", "name": "reservations", "element_type": "data_store"},
            ],
            "relations": [
                {
                    "id": "f1",
                    "source_id": "e1",
                    "target_id": "p1",
                    "data_item_id": str(data_item.id),
                },
                {
                    "id": "f2",
                    "source_id": "p1",
                    "target_id": "s1",
                    "data_item_id": str(data_item.id),
                },
            ],
        }
    )
    await diagram_service.update(
        project_id=project.id, diagram_id=diagram.id, expected_version=1, semantic_model=dfd_model
    )

    updated = await diagram_service.compute_layout(project_id=project.id, diagram_id=diagram.id)

    assert updated.layout_model is not None
    assert updated.layout_model["edges"]["f1"]["label_pos"] is not None


# ---- 出力(M8)
async def test_export_drawio_marks_diagram_exported(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=2)

    exported = await service.export(project_id=project.id, diagram_id=diagram.id, fmt="drawio")

    assert exported.filename == "component.drawio"
    assert exported.media_type == "application/xml"
    assert "<mxfile" in exported.content
    reloaded = await service.get(project_id=project.id, diagram_id=diagram.id)
    assert reloaded.status == "exported"
    assert reloaded.version == 2


async def test_export_svg_can_be_repeated_after_exported(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=2)
    await service.export(project_id=project.id, diagram_id=diagram.id, fmt="drawio")

    exported = await service.export(project_id=project.id, diagram_id=diagram.id, fmt="svg")

    assert exported.media_type == "image/svg+xml"
    assert exported.content.startswith("<svg")


async def test_export_rejects_diagram_that_is_not_approved(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)

    with pytest.raises(UmlDiagramNotApprovedError):
        await service.export(project_id=project.id, diagram_id=diagram.id, fmt="svg")


async def test_export_filename_replaces_unsafe_characters_in_subject(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    service = UmlDiagramService(db_session)
    diagram = await _laid_out_diagram(db_session, project.id)
    diagram.subject = "DF-1: POST /api/v1/reservations"
    await db_session.commit()
    await service.approve(project_id=project.id, diagram_id=diagram.id, expected_version=2)

    exported = await service.export(project_id=project.id, diagram_id=diagram.id, fmt="svg")

    assert exported.filename == "component_DF-1_ POST _api_v1_reservations.svg"
