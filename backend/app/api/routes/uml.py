import uuid

from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import Response

from app.api.deps import CurrentProjectDep, SessionDep
from app.api.responses import content_disposition
from app.schemas.data_item import DataItemCreate, DataItemRead, DataItemUpdate
from app.schemas.uml_diagram import UmlDiagramApprove, UmlDiagramRead, UmlDiagramUpdate
from app.schemas.uml_generation import (
    DfdSubjectRead,
    UmlCandidatesRead,
    UmlGenerateRequest,
    UmlGenerationRunRead,
)
from app.services.data_item_service import DataItemService
from app.services.uml_diagram_service import ExportFormat, UmlDiagramService
from app.services.uml_generation_service import (
    SubjectRequest,
    UmlGenerationService,
    run_uml_generation,
)
from app.uml.validation import ValidationResult

# project_idをprefixに含める(既存のprojects.pyはエンドポイント側にproject_idを書く方式だが、
# UML設計図パイプラインはdocs/internal_design.md 3.3節②の設計時点から"/projects/{id}/uml/..."と
# いう独立したサブツリーとして扱っており、diagrams/data-itemsどちらのエンドポイントも必ず
# project配下にネストするため、ここではprefixにproject_idを含めて宣言する)。
router = APIRouter(prefix="/projects/{project_id}/uml", tags=["uml"])


@router.post(
    "/diagrams", response_model=UmlGenerationRunRead, status_code=status.HTTP_202_ACCEPTED
)
async def generate_diagrams(
    payload: UmlGenerateRequest,
    session: SessionDep,
    current_project: CurrentProjectDep,
    background_tasks: BackgroundTasks,
) -> UmlGenerationRunRead:
    """UML図のAI生成(M1)を受け付け、バックグラウンドで実行する。対象の図は`generating`になり、
    完了すると`completed`/`failed`になる(FEは`GET /diagrams`をポーリングする)。同じ対象の図が
    既にあれば上書きする。戻り値は生成履歴(実行中)で、止まった理由は`GET /generation-runs`で
    確認できる。background taskにはproject_id・run_idの値だけを渡す
    (doc生成の`POST /projects/{id}/generate`と同じ理由)。"""
    run = await UmlGenerationService(session).request_generation(
        project_id=current_project.id,
        notation=payload.notation,
        subjects=[SubjectRequest(subject=s.subject, tables=s.tables) for s in payload.subjects],
    )
    background_tasks.add_task(run_uml_generation, current_project.id, run.id)
    return UmlGenerationRunRead.model_validate(run)


@router.get("/diagrams", response_model=list[UmlDiagramRead])
async def list_diagrams(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[UmlDiagramRead]:
    """プロジェクトのUML図一覧を取得する(更新日時の降順)。"""
    diagrams = await UmlDiagramService(session).list_for_project(current_project.id)
    return [UmlDiagramRead.model_validate(d) for d in diagrams]


@router.get("/candidates", response_model=UmlCandidatesRead)
async def list_generation_candidates(
    session: SessionDep, current_project: CurrentProjectDep
) -> UmlCandidatesRead:
    """生成対象の候補(DFDの処理・ERのテーブル)を、内部設計書の見出しから列挙する。"""
    candidates = await UmlGenerationService(session).list_candidates(current_project.id)
    return UmlCandidatesRead(
        internal_design_version=candidates.internal_design_version,
        dfd_subjects=[DfdSubjectRead(code=s.code, title=s.title) for s in candidates.dfd_subjects],
        er_tables=candidates.er_tables,
    )


@router.get("/generation-runs", response_model=list[UmlGenerationRunRead])
async def list_generation_runs(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[UmlGenerationRunRead]:
    """UML図のAI生成の履歴を新しい順に取得する(止まった理由と再度の生成指示が必要な旨を含む)。"""
    runs = await UmlGenerationService(session).list_runs(current_project.id)
    return [UmlGenerationRunRead.model_validate(r) for r in runs]


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
    """UML図の意味モデル全体(と、任意で配置)を更新する(楽観ロック。versionが不一致の場合は409)。"""
    diagram = await UmlDiagramService(session).update(
        project_id=current_project.id,
        diagram_id=diagram_id,
        expected_version=payload.version,
        semantic_model=payload.semantic_model,
        layout_model=payload.layout_model,
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


@router.post("/diagrams/{diagram_id}/layout", response_model=UmlDiagramRead)
async def compute_diagram_layout(
    diagram_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> UmlDiagramRead:
    """UML図の自動レイアウト(M6)を実行し、`layout_model`を保存して返す。
    要素数上限超過・M4構造検証エラーの場合は400(実行前チェック、Phase 9)。"""
    diagram = await UmlDiagramService(session).compute_layout(
        project_id=current_project.id, diagram_id=diagram_id
    )
    return UmlDiagramRead.model_validate(diagram)


@router.post("/diagrams/{diagram_id}/approve", response_model=UmlDiagramRead)
async def approve_diagram(
    diagram_id: uuid.UUID,
    payload: UmlDiagramApprove,
    session: SessionDep,
    current_project: CurrentProjectDep,
) -> UmlDiagramRead:
    """UML図を承認する(M7)。versionの不一致・承認できない状態は409、
    配置が無い・検証エラーがある場合は400。"""
    diagram = await UmlDiagramService(session).approve(
        project_id=current_project.id, diagram_id=diagram_id, expected_version=payload.version
    )
    return UmlDiagramRead.model_validate(diagram)


async def _export(
    diagram_id: uuid.UUID,
    fmt: ExportFormat,
    session: SessionDep,
    current_project: CurrentProjectDep,
) -> Response:
    exported = await UmlDiagramService(session).export(
        project_id=current_project.id, diagram_id=diagram_id, fmt=fmt
    )
    return Response(
        content=exported.content,
        media_type=exported.media_type,
        headers={"Content-Disposition": content_disposition(exported.filename)},
    )


@router.get("/diagrams/{diagram_id}/export/drawio")
async def export_diagram_drawio(
    diagram_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> Response:
    """承認済みのUML図を.drawioとしてダウンロードする(M8)。承認されていなければ409。
    出力に成功すると状態が`exported`になる。"""
    return await _export(diagram_id, "drawio", session, current_project)


@router.get("/diagrams/{diagram_id}/export/svg")
async def export_diagram_svg(
    diagram_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> Response:
    """承認済みのUML図をSVGとしてダウンロードする(M8。draw.ioと同じエンジンで書き出す)。"""
    return await _export(diagram_id, "svg", session, current_project)


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
