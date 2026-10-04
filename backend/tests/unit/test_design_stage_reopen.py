"""DFD・データ辞書の編集による承認済みの段階2の差し戻し(Phase 17)と、ER の編集による承認済みの
段階3の差し戻し(Phase 18)と、構成図の編集による承認済みの段階4の差し戻し(Phase 19)のテスト。

SUT: DesignStageService.mark_edited(app/services/design_stage_service.py)、
     UmlDiagramService.update / compute_layout(app/services/uml_diagram_service.py)、
     DataItemService.create / update / delete(app/services/data_item_service.py)、
     DATA_FLOW_STAGE(app/detailed_design/data_flow.py)、DATA_MODEL_STAGE(app/detailed_design/data_model.py)、
     STRUCTURE_STAGE(app/detailed_design/structure.py)
ドライバ: 各テスト関数(サービスのメソッドを直接呼ぶ)
スタブ不要 ── LLM を呼ばない。DB はインメモリSQLite(db_session)で、スタブにはしない(段階の行の
状態と版が変わることそのものが検証対象のため)。
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import (
    component_model,
    create_detailed_project,
    create_stage3_project,
    create_stage4_project,
    crud_model,
    data_flow_model,
    er_model,
    function_list_model,
    module_list_model,
)

from app.detailed_design import DATA_FLOW_STAGE, DATA_MODEL_STAGE, STRUCTURE_STAGE
from app.models.project import Project
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.data_item_service import DataItemService
from app.services.design_stage_service import DesignStageService
from app.services.uml_diagram_service import UmlDiagramService
from app.uml.domain import (
    ComponentSemanticModel,
    DfdSemanticModel,
    ErSemanticModel,
    SemanticModelAdapter,
)


async def _approved_stage2(session: AsyncSession) -> Project:
    """段階1・2を承認したプロジェクト(DFD を描くグループは無し)。"""
    project = await create_detailed_project(session)
    service = DesignStageService(session)
    await service.save(project, stage=1, expected_version=None, model=function_list_model())
    await service.approve(project, stage=1, expected_version=1)
    await service.save(project, stage=2, expected_version=None, model=data_flow_model())
    await service.approve(project, stage=2, expected_version=1)
    return project


def _dfd(item_id: uuid.UUID) -> DfdSemanticModel:
    model = SemanticModelAdapter.validate_python(
        {
            "notation": "dfd",
            "elements": [
                {"id": "e1", "name": "利用者", "element_type": "external_entity"},
                {"id": "F-01", "name": "F-01 予約を登録する", "element_type": "process"},
                {"id": "s1", "name": "reservations", "element_type": "data_store"},
            ],
            "relations": [
                {"id": "f1", "source_id": "e1", "target_id": "F-01", "data_item_id": str(item_id)},
                {"id": "f2", "source_id": "F-01", "target_id": "s1", "data_item_id": str(item_id)},
            ],
        }
    )
    assert isinstance(model, DfdSemanticModel)
    return model


async def _stage2(session: AsyncSession, project_id: uuid.UUID):
    return await DesignStageService(session).read(project_id, DATA_FLOW_STAGE)


async def test_dfd_save_reopens_approved_stage2(db_session: AsyncSession) -> None:
    """統合スモーク: 段階2を承認した後に DFD を保存すると、段階2がレビュー中に戻り版が増える。"""
    project = await _approved_stage2(db_session)
    project_id = project.id
    item = await DataItemService(db_session).create(project_id=project_id, name="予約", fields=[])
    await DesignStageService(db_session).approve(
        project, stage=2, expected_version=(await _stage2(db_session, project_id)).version or 0
    )
    diagram = await UmlDiagramRepository(db_session).create(
        project_id=project_id,
        view="dataflow",
        notation="dfd",
        semantic_model=_dfd(item.id).model_dump(mode="json"),
        subject="reservations",
    )
    await db_session.commit()
    before = await _stage2(db_session, project_id)

    await UmlDiagramService(db_session).update(
        project_id=project_id,
        diagram_id=diagram.id,
        expected_version=diagram.version,
        semantic_model=_dfd(item.id),
    )
    after = await _stage2(db_session, project_id)

    assert before.state == "approved"
    assert after.state == "reviewing"
    assert after.version == (before.version or 0) + 1


async def test_dfd_layout_reopens_approved_stage2(db_session: AsyncSession) -> None:
    project = await _approved_stage2(db_session)
    project_id = project.id
    item = await DataItemService(db_session).create(project_id=project_id, name="予約", fields=[])
    await DesignStageService(db_session).approve(
        project, stage=2, expected_version=(await _stage2(db_session, project_id)).version or 0
    )
    diagram = await UmlDiagramRepository(db_session).create(
        project_id=project_id,
        view="dataflow",
        notation="dfd",
        semantic_model=_dfd(item.id).model_dump(mode="json"),
        subject="reservations",
    )
    await db_session.commit()

    await UmlDiagramService(db_session).compute_layout(
        project_id=project_id, diagram_id=diagram.id
    )

    assert (await _stage2(db_session, project_id)).state == "reviewing"


async def test_data_item_edits_reopen_approved_stage2(db_session: AsyncSession) -> None:
    project = await _approved_stage2(db_session)
    project_id = project.id
    service = DataItemService(db_session)
    stages = DesignStageService(db_session)

    item = await service.create(project_id=project_id, name="予約", fields=[])
    created = await _stage2(db_session, project_id)
    await stages.approve(project, stage=2, expected_version=created.version or 0)
    await service.update(project_id=project_id, item_id=item.id, name="予約2", fields=[])
    updated = await _stage2(db_session, project_id)
    await stages.approve(project, stage=2, expected_version=updated.version or 0)
    await service.delete(project_id=project_id, item_id=item.id)
    deleted = await _stage2(db_session, project_id)

    assert (created.state, created.version) == ("reviewing", 2)
    assert (updated.state, updated.version) == ("reviewing", 3)
    assert (deleted.state, deleted.version) == ("reviewing", 4)


async def test_mark_edited_ignores_unapproved_and_simple_projects(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    simple = await create_detailed_project(db_session, mode="simple")
    stages = DesignStageService(db_session)

    assert await stages.mark_edited(project.id, DATA_FLOW_STAGE) is False
    assert await stages.mark_edited(simple.id, DATA_FLOW_STAGE) is False
    item = await DataItemService(db_session).create(project_id=simple.id, name="x", fields=[])
    assert item.name == "x"


async def test_er_save_and_layout_reopen_approved_stage3(db_session: AsyncSession) -> None:
    """ER を保存・自動レイアウトすると、承認済みの段階3がレビュー中に戻り版が増える。"""
    project = await create_stage3_project(db_session)
    project_id = project.id
    diagram = await UmlDiagramRepository(db_session).create(
        project_id=project_id, view="data", notation="er", semantic_model=er_model()
    )
    diagram.status = "approved"
    await db_session.commit()
    stages = DesignStageService(db_session)
    await stages.save(project, stage=DATA_MODEL_STAGE, expected_version=None, model=crud_model())
    approved = await stages.approve(project, stage=DATA_MODEL_STAGE, expected_version=1)
    service = UmlDiagramService(db_session)

    await service.update(
        project_id=project_id,
        diagram_id=diagram.id,
        expected_version=diagram.version,
        semantic_model=ErSemanticModel.model_validate(er_model()),
    )
    saved = await stages.read(project_id, DATA_MODEL_STAGE)
    diagram.status = "approved"  # ER の保存で ER 自体の承認もやり直しになるので、承認し直す
    await db_session.commit()
    await stages.approve(project, stage=DATA_MODEL_STAGE, expected_version=saved.version or 0)
    await service.compute_layout(project_id=project_id, diagram_id=diagram.id)
    laid_out = await stages.read(project_id, DATA_MODEL_STAGE)

    assert (approved.state, approved.version) == ("approved", 1)
    assert (saved.state, saved.version) == ("reviewing", 2)
    assert (laid_out.state, laid_out.version) == ("reviewing", 3)


async def test_component_save_and_layout_reopen_approved_stage4(db_session: AsyncSession) -> None:
    """構成図を保存・自動レイアウトすると、承認済みの段階4がレビュー中に戻り版が増える。"""
    project = await create_stage4_project(db_session)
    project_id = project.id
    diagram = await UmlDiagramRepository(db_session).create(
        project_id=project_id,
        view="structure",
        notation="component",
        semantic_model=component_model(),
    )
    diagram.status = "approved"
    await db_session.commit()
    stages = DesignStageService(db_session)
    await stages.save(
        project, stage=STRUCTURE_STAGE, expected_version=None, model=module_list_model()
    )
    approved = await stages.approve(project, stage=STRUCTURE_STAGE, expected_version=1)
    service = UmlDiagramService(db_session)

    await service.update(
        project_id=project_id,
        diagram_id=diagram.id,
        expected_version=diagram.version,
        semantic_model=ComponentSemanticModel.model_validate(component_model()),
    )
    saved = await stages.read(project_id, STRUCTURE_STAGE)
    diagram.status = "approved"  # 構成図の保存で構成図自体の承認もやり直しになるので、承認し直す
    await db_session.commit()
    await stages.approve(project, stage=STRUCTURE_STAGE, expected_version=saved.version or 0)
    await service.compute_layout(project_id=project_id, diagram_id=diagram.id)
    laid_out = await stages.read(project_id, STRUCTURE_STAGE)

    assert (approved.state, approved.version, approved.issues) == ("approved", 1, [])
    assert (saved.state, saved.version) == ("reviewing", 2)
    assert (laid_out.state, laid_out.version) == ("reviewing", 3)
