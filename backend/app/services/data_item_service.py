import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_item import DataItem
from app.repositories.data_item import DataItemRepository
from app.services.errors import DataItemNameConflictError, DataItemNotFoundError


class DataItemService:
    """プロジェクト共通のデータ辞書(DataItem)に対するCRUDユースケースを担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._data_items = DataItemRepository(session)

    async def list_for_project(self, project_id: uuid.UUID) -> list[DataItem]:
        """指定プロジェクトのデータ辞書一覧を取得する。"""
        return await self._data_items.list_for_project(project_id)

    async def create(
        self, *, project_id: uuid.UUID, name: str, fields: list[dict]
    ) -> DataItem:
        """データ項目を新規作成する。同一プロジェクト内に同名の項目があれば
        DataItemNameConflictError(409)にする(data_items.name一意制約に先立つ事前チェック)。"""
        if await self._data_items.find_by_name(project_id=project_id, name=name) is not None:
            raise DataItemNameConflictError(f"データ項目名が既に使用されています: {name}")
        data_item = await self._data_items.create(project_id=project_id, name=name, fields=fields)
        await self._session.commit()
        return data_item

    async def update(
        self, *, project_id: uuid.UUID, item_id: uuid.UUID, name: str, fields: list[dict]
    ) -> DataItem:
        """データ項目を更新する。名前を変更する場合、変更後の名前が既存の別項目と
        重複していればDataItemNameConflictError(409)にする。"""
        data_item = await self._get_owned(project_id=project_id, item_id=item_id)
        if name != data_item.name:
            existing = await self._data_items.find_by_name(project_id=project_id, name=name)
            if existing is not None and existing.id != item_id:
                raise DataItemNameConflictError(f"データ項目名が既に使用されています: {name}")
        data_item.name = name
        data_item.fields = fields
        await self._session.flush()
        await self._session.commit()
        # updated_at は server-side の onupdate=func.now() で決まるため、UPDATE後は
        # DBが計算した値を明示的に取り直す(理由はapp/services/uml_diagram_service.py参照)。
        await self._session.refresh(data_item)
        return data_item

    async def delete(self, *, project_id: uuid.UUID, item_id: uuid.UUID) -> None:
        """データ項目を削除する(このデータ項目を参照するDFDフローの整合性チェックは
        Phase 8の対象外 ── バリデーション実行時にUNKNOWN_DATA_ITEMとして検出される)。"""
        data_item = await self._get_owned(project_id=project_id, item_id=item_id)
        await self._data_items.delete(data_item)
        await self._session.commit()

    async def _get_owned(self, *, project_id: uuid.UUID, item_id: uuid.UUID) -> DataItem:
        data_item = await self._data_items.get_by_id(item_id, project_id=project_id)
        if data_item is None:
            raise DataItemNotFoundError(f"Data item {item_id} not found")
        return data_item
