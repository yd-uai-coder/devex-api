import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select

from app.models.change_request import ChangeRequest
from app.models.material_difference import MaterialDifference, MaterialExtraction
from app.repositories.base import CRUDRepository


class MaterialDifferenceRepository(CRUDRepository[MaterialDifference]):
    """資料から取り出した違いと、取り出しの直近の回(資料ごとに1行)の永続化。どの違いを残し、
    どれを置き換えるかを決めるのはサービスで、ここは行の作成・削除・読み出しを持つ。"""

    model = MaterialDifference

    async def list_for(self, material_id: uuid.UUID) -> list[MaterialDifference]:
        """資料の違い(並びの順)。"""
        stmt = (
            select(MaterialDifference)
            .where(MaterialDifference.material_id == material_id)
            .order_by(MaterialDifference.position)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_many_in_material(
        self, *, material_id: uuid.UUID, difference_ids: list[uuid.UUID]
    ) -> list[MaterialDifference]:
        """資料の違いのうち`difference_ids`のもの(並びの順。ほかの資料の違いは入らない)。"""
        stmt = (
            select(MaterialDifference)
            .where(
                MaterialDifference.material_id == material_id,
                MaterialDifference.id.in_(difference_ids),
            )
            .order_by(MaterialDifference.position)
        )
        return list((await self._session.scalars(stmt)).all())

    async def change_request_numbers(
        self, differences: list[MaterialDifference]
    ) -> dict[uuid.UUID, int]:
        """CR にした違いの id → その CR の番号(CR に結んでいない違いは入らない)。"""
        ids = {d.change_request_id for d in differences if d.change_request_id is not None}
        if not ids:
            return {}
        stmt = select(ChangeRequest.id, ChangeRequest.number).where(ChangeRequest.id.in_(ids))
        numbers = {row.id: row.number for row in await self._session.execute(stmt)}
        return {
            d.id: numbers[d.change_request_id]
            for d in differences
            if d.change_request_id is not None and d.change_request_id in numbers
        }

    async def get_in_material(
        self, *, material_id: uuid.UUID, difference_id: uuid.UUID
    ) -> MaterialDifference | None:
        """資料の違い1件(ほかの資料の違いは None)。"""
        return await self.find_one(material_id=material_id, id=difference_id)

    async def delete_with_statuses(self, material_id: uuid.UUID, statuses: list[str]) -> None:
        """資料の違いのうち、状態が`statuses`のものを消す。flushまで行う。"""
        await self._session.execute(
            delete(MaterialDifference).where(
                MaterialDifference.material_id == material_id,
                MaterialDifference.status.in_(statuses),
            )
        )
        await self._session.flush()

    async def next_position(self, material_id: uuid.UUID) -> int:
        """資料の違いの次の並び(残っている違いの後ろ)。"""
        stmt = select(func.max(MaterialDifference.position)).where(
            MaterialDifference.material_id == material_id
        )
        return int(await self._session.scalar(stmt) or 0) + 1

    async def add(self, **values: Any) -> MaterialDifference:
        """違いを1件足す。flushは呼び出し側がまとめて行う。足した日時はアプリで入れる(読み直さずに
        日時を出せるように)。"""
        difference = MaterialDifference(**values, created_at=datetime.now(UTC))
        self._session.add(difference)
        return difference

    async def extraction_for(self, material_id: uuid.UUID) -> MaterialExtraction | None:
        """資料の取り出しの直近の回。まだなら None。"""
        stmt = select(MaterialExtraction).where(MaterialExtraction.material_id == material_id)
        return await self._session.scalar(stmt)

    async def replace_extraction(
        self,
        *,
        material_id: uuid.UUID,
        baseline_number: int,
        freshness: dict[str, Any],
        generated_model: str | None,
        generated_by: uuid.UUID | None,
    ) -> MaterialExtraction:
        """取り出しの回を記録する(あれば上書き)。flushまで行う。"""
        extraction = await self.extraction_for(material_id)
        if extraction is None:
            extraction = MaterialExtraction(material_id=material_id)
            self._session.add(extraction)
        extraction.baseline_number = baseline_number
        extraction.freshness = freshness
        extraction.generated_model = generated_model
        extraction.generated_by = generated_by
        extraction.generated_at = datetime.now(UTC)
        await self._session.flush()
        return extraction
