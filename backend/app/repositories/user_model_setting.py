import uuid

from sqlalchemy import delete, select

from app.models.user_model_setting import UserModelSetting
from app.repositories.base import CRUDRepository


class UserModelSettingRepository(CRUDRepository[UserModelSetting]):
    """用途ごとのモデルの行(ユーザーの選択と、user_id が null のシステムの既定値)の永続化。"""

    model = UserModelSetting

    async def list_defaults(self) -> list[UserModelSetting]:
        """システムの既定値の行(user_id が null)を返す。"""
        stmt = select(UserModelSetting).where(UserModelSetting.user_id.is_(None))
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserModelSetting]:
        """ユーザーが選んだ用途の行を返す(選ばない用途は行が無い)。"""
        return await self.list_all(user_id=user_id)

    async def upsert_default(self, *, purpose: str, provider: str, model: str) -> UserModelSetting:
        """既定値の行を、無ければ作り、あれば provider とモデルを上書きする。"""
        stmt = select(UserModelSetting).where(
            UserModelSetting.user_id.is_(None), UserModelSetting.purpose == purpose
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = UserModelSetting(user_id=None, purpose=purpose, provider=provider, model=model)
            self._session.add(row)
        else:
            row.provider = provider
            row.model = model
        await self._session.flush()
        return row

    async def replace_for_user(
        self, user_id: uuid.UUID, choices: list[tuple[str, str, str]]
    ) -> list[UserModelSetting]:
        """ユーザーの選択を、渡した (用途, provider, モデル) の組で置き換える。
        渡さない用途の行は消す(既定値に従う)。"""
        await self._session.execute(
            delete(UserModelSetting).where(UserModelSetting.user_id == user_id)
        )
        rows = [
            UserModelSetting(user_id=user_id, purpose=purpose, provider=provider, model=model)
            for purpose, provider, model in choices
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return rows
