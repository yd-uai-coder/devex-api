import uuid

from app.models.design_stage import DesignStage
from app.repositories.base import CRUDRepository


class DesignStageRepository(CRUDRepository[DesignStage]):
    """DesignStageモデルに対する永続化操作をまとめるリポジトリ。状態の判定・楽観ロック・承認の
    条件はサービス層(app/services/design_stage_service.py)と純粋関数(app/detailed_design/)が担う。"""

    model = DesignStage

    async def create(
        self, *, project_id: uuid.UUID, stage: int, model: dict, status: str = "draft"
    ) -> DesignStage:
        """新しい段階の行を追加し、flushしてIDを確定させた状態で返す(versionは1)。"""
        row = DesignStage(project_id=project_id, stage=stage, model=model, status=status)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, *, project_id: uuid.UUID, stage: int) -> DesignStage | None:
        return await self.find_one(project_id=project_id, stage=stage)

    async def list_for_project(self, project_id: uuid.UUID) -> list[DesignStage]:
        """プロジェクトの段階を段階番号の順に返す(未着手の段階は含まない)。"""
        return await self.list_all(order_by=DesignStage.stage, project_id=project_id)
