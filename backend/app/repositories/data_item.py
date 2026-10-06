import uuid

from app.models.data_item import DataItem
from app.repositories.base import CRUDRepository


class DataItemRepository(CRUDRepository[DataItem]):
    """DataItem(プロジェクト共通のデータ辞書)モデルに対する永続化操作をまとめるリポジトリ。"""

    model = DataItem

    async def create(
        self, *, project_id: uuid.UUID, name: str, fields: list[dict] | None = None
    ) -> DataItem:
        """新規データ項目をセッションに追加し、flushしてIDを確定させた状態で返す。"""
        data_item = DataItem(project_id=project_id, name=name, fields=fields or [])
        self._session.add(data_item)
        await self._session.flush()
        return data_item

    async def get_by_id(self, item_id: uuid.UUID, *, project_id: uuid.UUID) -> DataItem | None:
        """データ項目IDとプロジェクトIDの両方が一致するものだけを取得する
        (ProjectRepository.get_by_idのuser_idスコープと同じ考え方)。"""
        return await self.find_one(id=item_id, project_id=project_id)

    async def list_for_project(self, project_id: uuid.UUID) -> list[DataItem]:
        """指定プロジェクトのデータ辞書一覧を名前順で取得する。"""
        return await self.list_all(order_by=DataItem.name, project_id=project_id)

    async def find_by_name(self, *, project_id: uuid.UUID, name: str) -> DataItem | None:
        """同一プロジェクト内の名前一意性チェック用。"""
        return await self.find_one(project_id=project_id, name=name)
