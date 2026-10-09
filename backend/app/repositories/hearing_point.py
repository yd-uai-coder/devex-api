import uuid
from collections.abc import Iterable

from app.hearing import PointState
from app.models.hearing_point import HearingPoint
from app.repositories.base import CRUDRepository


class HearingPointRepository(CRUDRepository[HearingPoint]):
    """HearingPointモデルに対する永続化操作をまとめるリポジトリ。取り込みの規則・完了の判定は
    純粋関数(app/hearing/points.py)が担う。"""

    model = HearingPoint

    async def list_for_project(self, project_id: uuid.UUID) -> list[HearingPoint]:
        return await self.list_all(project_id=project_id)

    async def save_states(self, project_id: uuid.UUID, states: Iterable[PointState]) -> None:
        """観点の状態を、観点のキーで突き合わせて更新する(無い観点は行を足す)。flushまで行う。"""
        rows = {row.point_key: row for row in await self.list_for_project(project_id)}
        for state in states:
            row = rows.get(state.key)
            if row is None:
                row = HearingPoint(project_id=project_id, point_key=state.key)
                self._session.add(row)
            row.status = state.status
            row.summary = state.summary
            row.evidence = list(state.evidence)
        await self._session.flush()
