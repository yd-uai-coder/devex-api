import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentProjectDep, SessionDep
from app.schemas.data_item import DataItemCreate, DataItemRead, DataItemUpdate
from app.schemas.uml_diagram import UmlDiagramCreate, UmlDiagramRead, UmlDiagramUpdate
from app.services.data_item_service import DataItemService
from app.services.uml_diagram_service import UmlDiagramService
from app.uml.validation import ValidationResult

# project_idをprefixに含める(既存のprojects.pyはエンドポイント側にproject_idを書く方式だが、
# UML設計図パイプラインはdocs/internal_design.md 3.3節②の設計時点から"/projects/{id}/uml/..."と
# いう独立したサブツリーとして扱っており、diagrams/data-itemsどちらのエンドポイントも必ず
# project配下にネストするため、ここではprefixにproject_idを含めて宣言する)。
router = APIRouter(prefix="/projects/{project_id}/uml", tags=["uml"])


@router.post("/diagrams", response_model=UmlDiagramRead, status_code=status.HTTP_201_CREATED)
async def create_diagram(
    payload: UmlDiagramCreate, session: SessionDep, current_project: CurrentProjectDep
) -> UmlDiagramRead:
    """UML図を新規作成する(Phase 8時点ではAI生成トリガーのプレースホルダーとして、
    要素・関係が空のdraftを返す。実AI生成はPhase 10で追加)。"""
    diagram = await UmlDiagramService(session).create(
        project_id=current_project.id, notation=payload.notation
    )
    return UmlDiagramRead.model_validate(diagram)


@router.get("/diagrams/{diagram_id}", response_model=UmlDiagramRead)
async def get_diagram(
    diagram_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> UmlDiagramRead:
    """UML図を1件取得する。"""
    diagram = await UmlDiagramService(session).get(
        project_id=current_project.id, diagram_id=diagram_id
    )
    return UmlDiagramRead.model_validate(diagram)


@router.put("/diagrams/{diagram_id}", response_model=UmlDiagramRead)
async def update_diagram(
    diagram_id: uuid.UUID,
    payload: UmlDiagramUpdate,
    session: SessionDep,
    current_project: CurrentProjectDep,
) -> UmlDiagramRead:
    """UML図の意味モデル全体を更新する(楽観ロック。versionが不一致の場合は409)。"""
    diagram = await UmlDiagramService(session).update(
        project_id=current_project.id,
        diagram_id=diagram_id,
        expected_version=payload.version,
        semantic_model=payload.semantic_model,
    )
    return UmlDiagramRead.model_validate(diagram)


@router.post("/diagrams/{diagram_id}/validate", response_model=ValidationResult)
async def validate_diagram(
    diagram_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> ValidationResult:
    """UML図を検証し、エラー・警告の一覧を返す(例外は投げない。常に200)。"""
    return await UmlDiagramService(session).validate(
        project_id=current_project.id, diagram_id=diagram_id
    )


@router.get("/data-items", response_model=list[DataItemRead])
async def list_data_items(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[DataItemRead]:
    """プロジェクト共通のデータ辞書一覧を取得する。"""
    items = await DataItemService(session).list_for_project(current_project.id)
    return [DataItemRead.model_validate(item) for item in items]


@router.post("/data-items", response_model=DataItemRead, status_code=status.HTTP_201_CREATED)
async def create_data_item(
    payload: DataItemCreate, session: SessionDep, current_project: CurrentProjectDep
) -> DataItemRead:
    """データ項目を新規作成する(同一プロジェクト内で名前が重複する場合は409)。"""
    item = await DataItemService(session).create(
        project_id=current_project.id,
        name=payload.name,
        fields=[f.model_dump() for f in payload.fields],
    )
    return DataItemRead.model_validate(item)


@router.put("/data-items/{item_id}", response_model=DataItemRead)
async def update_data_item(
    item_id: uuid.UUID,
    payload: DataItemUpdate,
    session: SessionDep,
    current_project: CurrentProjectDep,
) -> DataItemRead:
    """データ項目を更新する(name/fieldsを丸ごと置き換える)。"""
    item = await DataItemService(session).update(
        project_id=current_project.id,
        item_id=item_id,
        name=payload.name,
        fields=[f.model_dump() for f in payload.fields],
    )
    return DataItemRead.model_validate(item)


@router.delete("/data-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_item(
    item_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> None:
    """データ項目を削除する。"""
    await DataItemService(session).delete(project_id=current_project.id, item_id=item_id)
