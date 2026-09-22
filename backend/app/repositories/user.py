from app.models.user import User
from app.repositories.base import CRUDRepository


class UserRepository(CRUDRepository[User]):
    """Userモデルに対する永続化操作をまとめるリポジトリ。"""

    model = User

    async def get_by_email(self, email: str) -> User | None:
        """メールアドレスの完全一致でユーザーを1件検索する。"""
        return await self.find_one(email=email)

    async def create(
        self, *, email: str, hashed_password: str, full_name: str | None = None
    ) -> User:
        """新規ユーザーをセッションに追加し、flushしてIDを確定させた状態で返す。"""
        user = User(email=email, hashed_password=hashed_password, full_name=full_name)
        self._session.add(user)
        await self._session.flush()
        return user
