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
from app.services.material_service import MaterialService, MaterialView

# 束・CR と同じく、プロジェクト配下の独立したサブツリーとしてprefixにproject_idを含める
router = APIRouter(prefix="/projects/{project_id}/materials", tags=["materials"])


def _summary(view: MaterialView) -> MaterialSummaryRead:
    return MaterialSummaryRead.model_validate({**_columns(view), "length": view.length})


def _read(view: MaterialView) -> MaterialRead:
    return MaterialRead.model_validate(
        {**_columns(view), "length": view.length, "body": view.material.body}
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
