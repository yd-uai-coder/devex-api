import contextlib
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material
from app.models.project import Project
from app.repositories.material import MaterialRepository
from app.schemas.material import MaterialCreate, MaterialMeta
from app.services.detailed_mode import require_detailed
from app.services.errors import (
    FileTooLargeError,
    LlmUnavailableError,
    MaterialNotFoundError,
    MaterialTooLongError,
    MaterialUnreadableError,
    TooManyMaterialsError,
    UnsupportedFileTypeError,
)
from app.services.intake_file_processor import ALLOWED_FILE_TYPES, extract_text, file_type_of
from app.services.llm_resolver import LlmResolver

logger = structlog.get_logger(__name__)

# 1プロジェクトの資料の数・本文の文字数・ファイルの大きさの上限。本文は切り詰めずに断る(違いの根拠の
# 引用を本文で確かめるため)。ファイルの大きさは、API 全体のリクエストの上限(2MB)に合わせる
MAX_MATERIALS_PER_PROJECT = 50
MAX_MATERIAL_CHARS = 20_000
MAX_MATERIAL_FILE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class MaterialView:
    """資料1件と本文の文字数(一覧は本文を読まないので、数だけを添える)。"""

    material: Material
    length: int


class MaterialService:
    """資料(議事録・メール・資料)の追加・一覧・取得・削除(docs/external_design.md 2.17節)。

    本文は貼り付けか、txt / md / pdf から取り出す(作成時の添付資料と同じ関数。PDF は持ち主の
    Gemini で読む)。取り出せないものは足さない(作成時の添付資料と違い、失敗の行は残さない)。
    ヒアリングの根拠(チャットの履歴)には入れない。詳細設計モードだけ(CR と同じ)。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._materials = MaterialRepository(session)

    async def create_from_text(
        self, project: Project, payload: MaterialCreate, *, created_by: uuid.UUID | None
    ) -> MaterialView:
        """貼り付けた本文で資料を足す。"""
        await self._require_room(project)
        return await self._create(
            project, payload, payload.body, source="paste", filename=None, created_by=created_by
        )

    async def create_from_file(
        self,
        project: Project,
        meta: MaterialMeta,
        *,
        filename: str,
        data: bytes,
        created_by: uuid.UUID | None,
    ) -> MaterialView:
        """ファイル(txt / md / pdf)の本文で資料を足す。形式・大きさ・数は、本文を取り出す前に
        確かめる(PDF を読む AI を無駄に呼ばない)。"""
        file_type = file_type_of(filename)
        if file_type not in ALLOWED_FILE_TYPES:
            raise UnsupportedFileTypeError(f"対応していないファイル形式です: {filename}")
        if len(data) > MAX_MATERIAL_FILE_BYTES:
            raise FileTooLargeError(f"ファイルサイズが上限を超えています: {filename}")
        await self._require_room(project)
        llm = None
        if file_type == "pdf":
            with contextlib.suppress(LlmUnavailableError):
                llm = (await LlmResolver(self._session).resolve_pdf_reader(project.user_id)).llm
        text, error = await extract_text(file_type=file_type, data=data, llm=llm, max_chars=None)
        if text is None or not text.strip():
            raise MaterialUnreadableError(
                f"ファイルから本文を取り出せませんでした: {filename}"
                + (f"({error})" if error else "")
            )
        return await self._create(
            project, meta, text.strip(), source=file_type, filename=filename, created_by=created_by
        )

    async def list_for_project(self, project: Project) -> list[MaterialView]:
        """プロジェクトの資料(足した順の新しい方から。本文は読まない)。"""
        require_detailed(project, "Materials")
        return [
            MaterialView(material, length)
            for material, length in await self._materials.list_with_length(project.id)
        ]

    async def get(self, project: Project, material_id: uuid.UUID) -> MaterialView:
        """資料1件(本文つき)。無ければ MaterialNotFoundError。"""
        material = await self._load(project, material_id)
        return MaterialView(material, len(material.body))

    async def delete(self, project: Project, material_id: uuid.UUID) -> None:
        """資料を消す。資料から作った CR は残る(背景の出どころは CR の側の文)。"""
        material = await self._load(project, material_id)
        await self._materials.delete(material)
        await self._session.commit()
        logger.info("material_deleted", project_id=str(project.id), material_id=str(material_id))

    async def _require_room(self, project: Project) -> None:
        require_detailed(project, "Materials")
        if await self._materials.count(project_id=project.id) >= MAX_MATERIALS_PER_PROJECT:
            raise TooManyMaterialsError(f"資料は最大{MAX_MATERIALS_PER_PROJECT}件までです")

    async def _create(
        self,
        project: Project,
        meta: MaterialMeta,
        body: str,
        *,
        source: str,
        filename: str | None,
        created_by: uuid.UUID | None,
    ) -> MaterialView:
        if len(body) > MAX_MATERIAL_CHARS:
            raise MaterialTooLongError(
                f"本文が長すぎます({len(body)}文字。上限は{MAX_MATERIAL_CHARS}文字)。"
                "分けて足してください。"
            )
        material = await self._materials.create(
            project_id=project.id,
            kind=meta.kind,
            title=meta.title,
            material_date=meta.material_date,
            body=body,
            source=source,
            filename=filename,
            created_by=created_by,
        )
        await self._session.commit()
        logger.info("material_created", project_id=str(project.id), material_id=str(material.id))
        return MaterialView(material, len(body))

    async def _load(self, project: Project, material_id: uuid.UUID) -> Material:
        require_detailed(project, "Materials")
        material = await self._materials.get_in_project(
            project_id=project.id, material_id=material_id
        )
        if material is None:
            raise MaterialNotFoundError(f"Material {material_id} not found")
        return material
