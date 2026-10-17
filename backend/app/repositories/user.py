from app.models.user import User
from app.repositories.base import CRUDRepository


class UserRepository(CRUDRepository[User]):
    """Userモデルに対する永続化操作をまとめるリポジトリ。"""

    model = User

    async def get_by_email(self, email: str) -> User | None:
        """メールアドレスの完全一致でユーザーを1件検索する。"""
        return await self.find_one(email=email)

    async def get_by_google_sub(self, google_sub: str) -> User | None:
        """連携した Google アカウントの識別子(sub)でユーザーを1件検索する。"""
        return await self.find_one(google_sub=google_sub)

    async def create(
        self,
        *,
        email: str,
        hashed_password: str | None,
        user_name: str | None = None,
        google_sub: str | None = None,
    ) -> User:
        """新規ユーザーをセッションに追加し、flushしてIDを確定させた状態で返す。"""
        user = User(
            email=email,
            hashed_password=hashed_password,
            user_name=user_name,
            google_sub=google_sub,
        )
        self._session.add(user)
        await self._session.flush()
        return user
