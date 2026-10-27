import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import CurrentProjectDep, CurrentUserDep, SessionDep
from app.schemas.material import (
    MaterialCreate,
    MaterialKind,
    MaterialMeta,
    MaterialRead,
    MaterialSummaryRead,
    MaterialTitle,
)
from app.schemas.material_difference import (
    MaterialDifferenceRead,
    MaterialDifferencesRead,
    MaterialDifferenceUpdate,
    MaterialExtractionRead,
)
from app.services.material_difference_service import DifferencesView, MaterialDifferenceService
from app.services.material_service import MaterialService, MaterialView

# 束・CR と同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/materials", tags=["materials"])


def _summary(view: MaterialView) -> MaterialSummaryRead:
    return MaterialSummaryRead.model_validate({**_columns(view), "length": view.length})


def _read(view: MaterialView) -> MaterialRead:
    return MaterialRead.model_validate(
        {**_columns(view), "length": view.length, "body": view.material.body}
    )


def _differences(view: DifferencesView) -> MaterialDifferencesRead:
    extraction = view.extraction
    return MaterialDifferencesRead(
        material_id=view.material.id,
        extraction=(
            None if extraction is None else MaterialExtractionRead.model_validate(extraction)
        ),
        latest_baseline_number=view.latest_baseline_number,
        differences=[MaterialDifferenceRead.model_validate(d) for d in view.differences],
    )


def _columns(view: MaterialView) -> dict[str, object]:
    m = view.material
    return {
        "id": m.id,
        "kind": m.kind,
        "title": m.title,
        "material_date": m.material_date,
        "source": m.source,
        "filename": m.filename,
        "created_by": m.created_by,
        "created_at": m.created_at,
    }


@router.post("", response_model=MaterialRead, status_code=status.HTTP_201_CREATED)
async def create_material(
    payload: MaterialCreate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> MaterialRead:
    """本文を貼り付けて資料を足す。簡易ドキュメントモード・資料が上限の数は409、本文が長すぎれば
    422。"""
    view = await MaterialService(session).create_from_text(
        current_project, payload, created_by=current_user.id
    )
    return _read(view)


@router.post("/upload", response_model=MaterialRead, status_code=status.HTTP_201_CREATED)
async def upload_material(
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
    file: Annotated[UploadFile, File()],
    kind: Annotated[MaterialKind, Form()],
    title: Annotated[MaterialTitle, Form()],
    material_date: Annotated[date, Form()],
) -> MaterialRead:
    """ファイル(txt / md / pdf)の本文で資料を足す。形式が違えば400、大きすぎれば400、本文を
    取り出せない・長すぎれば422。"""
    meta = MaterialMeta(kind=kind, title=title, material_date=material_date)
    view = await MaterialService(session).create_from_file(
        current_project,
        meta,
        filename=file.filename or "unnamed",
        data=await file.read(),
        created_by=current_user.id,
    )
    return _read(view)


@router.get("", response_model=list[MaterialSummaryRead])
async def list_materials(
    session: SessionDep, current_project: CurrentProjectDep
) -> list[MaterialSummaryRead]:
    """プロジェクトの資料を、足した順の新しい方から返す(本文なし)。"""
    return [_summary(v) for v in await MaterialService(session).list_for_project(current_project)]


@router.get("/{material_id}", response_model=MaterialRead)
async def get_material(
    material_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> MaterialRead:
    """資料1件(本文つき)。無ければ404。"""
    return _read(await MaterialService(session).get(current_project, material_id))


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material(
    material_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> None:
    """資料を消す(資料から作った CR は残る)。無ければ404。"""
    await MaterialService(session).delete(current_project, material_id)


@router.post("/{material_id}/differences", response_model=MaterialDifferencesRead)
async def extract_material_differences(
    material_id: uuid.UUID,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> MaterialDifferencesRead:
    """資料と最新の束の違いを LLM で取り出す(やり直すと、未確認と弾いたを置き換える)。束が古くても
    止めず、比べた束の鮮度を返す。束が無ければ409、資料が無ければ404。"""
    view = await MaterialDifferenceService(session).extract(
        current_project, material_id, user_id=current_user.id
    )
    return _differences(view)


@router.get("/{material_id}/differences", response_model=MaterialDifferencesRead)
async def list_material_differences(
    material_id: uuid.UUID, session: SessionDep, current_project: CurrentProjectDep
) -> MaterialDifferencesRead:
    """資料の違いの一覧(取り出しの回の記録と、今の最新の束の番号つき)。資料が無ければ404。"""
    view = await MaterialDifferenceService(session).list_for(current_project, material_id)
    return _differences(view)


@router.patch("/{material_id}/differences/{difference_id}", response_model=MaterialDifferenceRead)
async def update_material_difference(
    material_id: uuid.UUID,
    difference_id: uuid.UUID,
    payload: MaterialDifferenceUpdate,
    session: SessionDep,
    current_project: CurrentProjectDep,
    current_user: CurrentUserDep,
) -> MaterialDifferenceRead:
    """違いを捨てる(理由つき)・捨てたものを未確認に戻す。今の状態でできなければ409、無ければ
    404。"""
    service = MaterialDifferenceService(session)
    if payload.action == "dismiss":
        assert payload.reason is not None  # スキーマで確かめた
        difference = await service.dismiss(
            current_project,
            material_id,
            difference_id,
            reason=payload.reason,
            actor_id=current_user.id,
        )
    else:
        difference = await service.restore(current_project, material_id, difference_id)
    return MaterialDifferenceRead.model_validate(difference)
