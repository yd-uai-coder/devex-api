import uuid

from sqlalchemy import select

from app.models.uml_generation_run import UmlGenerationRun
from app.repositories.base import CRUDRepository


class UmlGenerationRunRepository(CRUDRepository[UmlGenerationRun]):
    """UmlGenerationRun(UML図のAI生成リクエストの履歴)に対する永続化操作をまとめるリポジトリ。"""

    model = UmlGenerationRun

    async def create(
        self, *, project_id: uuid.UUID, notation: str, requested: list[dict]
    ) -> UmlGenerationRun:
        """生成リクエストを'running'状態で追加し、flushしてIDを確定させた状態で返す。"""
        run = UmlGenerationRun(
            project_id=project_id,
            notation=notation,
            requested=requested,
            status="running",
            results=[],
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_by_id(
        self, run_id: uuid.UUID, *, project_id: uuid.UUID
    ) -> UmlGenerationRun | None:
        """履歴IDとプロジェクトIDの両方が一致するものだけを取得する。"""
        return await self.find_one(id=run_id, project_id=project_id)

    async def list_recent(
        self, project_id: uuid.UUID, *, limit: int = 20
    ) -> list[UmlGenerationRun]:
        """指定プロジェクトの生成履歴を新しい順に最大`limit`件取得する。"""
        stmt = (
            select(UmlGenerationRun)
            .filter_by(project_id=project_id)
            .order_by(UmlGenerationRun.started_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
