from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base


class CRUDRepository[ModelType: Base]:
    """特定のORMモデルに対する基本的なCRUD操作をまとめた汎用リポジトリ基底クラス。"""

    model: type[ModelType]

    def __init__(self, session: AsyncSession) -> None:
        # session: このリポジトリが利用する非同期DBセッション
        self._session = session

    async def get_by_id(self, id_: Any) -> ModelType | None:
        """主キーでモデルを1件取得する。見つからない場合はNoneを返す。"""
        return await self._session.get(self.model, id_)

    async def find_one(self, **filters: Any) -> ModelType | None:
        """任意のカラム条件を指定して1件検索する。0件の場合はNoneを返す。"""
        stmt = select(self.model).filter_by(**filters).options(*self._default_options())
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, *, order_by: Any = None, **filters: Any) -> list[ModelType]:
        """任意のカラム条件を指定して複数件検索し、リストとして返す。"""
        stmt = select(self.model).filter_by(**filters).options(*self._default_options())
        if order_by is not None:
            # order_byが指定された場合のみソート条件を付与する
            stmt = stmt.order_by(order_by)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_or_create(
        self, *, lookup: dict[str, Any], defaults: dict[str, Any] | None = None
    ) -> ModelType:
        """lookup条件に一致するレコードがあれば返し、無ければdefaultsを加えて新規作成する。"""
        existing = await self.find_one(**lookup)
        if existing is not None:
            return existing
        # 新規作成時はlookup条件とdefaultsをマージしてインスタンス化する
        obj = self.model(**lookup, **(defaults or {}))
        self._session.add(obj)
        await self._session.flush()
        return obj

    async def count(self, **filters: Any) -> int:
        """任意のカラム条件に一致するレコード件数を返す。"""
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def delete(self, obj: ModelType) -> None:
        """指定したインスタンスをセッションから削除し、即座にflushする。"""
        await self._session.delete(obj)
        await self._session.flush()

    def _default_options(self) -> tuple[Any, ...]:
        """find_one/list_allに適用するSQLAlchemyのロードオプション（Eagerロード等）。サブクラスで上書きする。"""
        return ()
