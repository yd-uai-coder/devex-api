import uuid

from app.models.user_api_key import UserApiKey
from app.repositories.base import CRUDRepository


class UserApiKeyRepository(CRUDRepository[UserApiKey]):
    """provider ごとの API キー(暗号文と末尾の4文字)の永続化。"""

    model = UserApiKey

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserApiKey]:
        """ユーザーが登録したキーを返す。"""
        return await self.list_all(user_id=user_id)

    async def get_for_user(self, user_id: uuid.UUID, provider: str) -> UserApiKey | None:
        """ユーザーの、その provider のキーを返す(無ければ None)。"""
        return await self.find_one(user_id=user_id, provider=provider)

    async def upsert(
        self, *, user_id: uuid.UUID, provider: str, encrypted_key: str, key_hint: str
    ) -> UserApiKey:
        """その provider のキーを、無ければ作り、あれば暗号文と末尾を置き換える。"""
        row = await self.get_for_user(user_id, provider)
        if row is None:
            row = UserApiKey(
                user_id=user_id, provider=provider, encrypted_key=encrypted_key, key_hint=key_hint
            )
            self._session.add(row)
        else:
            row.encrypted_key = encrypted_key
            row.key_hint = key_hint
        await self._session.flush()
        return row
