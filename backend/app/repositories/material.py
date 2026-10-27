import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import defer

from app.models.material import Material
from app.repositories.base import CRUDRepository


class MaterialRepository(CRUDRepository[Material]):
    """資料(議事録・メール・資料)の永続化。一覧は本文を読まず、本文の文字数だけを数える。"""

    model = Material

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        kind: str,
        title: str,
        material_date: date,
        body: str,
        source: str,
        filename: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> Material:
        """資料を1件足す。flushまで行う。足した日時はアプリで入れる(作った資料をそのまま返す
        とき、読み直さずに日時を出せるように)。"""
        material = Material(
            project_id=project_id,
            kind=kind,
            title=title,
            material_date=material_date,
            body=body,
            source=source,
            filename=filename,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        self._session.add(material)
        await self._session.flush()
        return material

    async def list_with_length(self, project_id: uuid.UUID) -> list[tuple[Material, int]]:
        """プロジェクトの資料を、足した順の新しい方から、本文の文字数と組にして返す(本文は
        読まない)。"""
        stmt = (
            select(Material, func.length(Material.body))
            .where(Material.project_id == project_id)
            .order_by(Material.created_at.desc())
            .options(defer(Material.body))
        )
        return [(m, int(n or 0)) for m, n in (await self._session.execute(stmt)).tuples()]

    async def get_in_project(
        self, *, project_id: uuid.UUID, material_id: uuid.UUID
    ) -> Material | None:
        """プロジェクトの資料1件(ほかのプロジェクトの資料は None)。"""
        return await self.find_one(project_id=project_id, id=material_id)
