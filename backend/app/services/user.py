import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.errors import UserAlreadyExistsError


class UserService:
    """ユーザーの参照・新規作成に関するユースケースを担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._repo = UserRepository(session)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """IDを指定してユーザーを1件取得する。存在しない場合はNoneを返す。"""
        return await self._repo.get_by_id(user_id)

    async def get_by_email(self, email: str) -> User | None:
        """メールアドレスを指定してユーザーを1件取得する。存在しない場合はNoneを返す。"""
        return await self._repo.get_by_email(email)

    async def create_user(self, *, email: str, password: str, full_name: str | None = None) -> User:
        """メールアドレスの重複が無いことを確認し、パスワードをハッシュ化して新規ユーザーを登録する。"""
        existing = await self._repo.get_by_email(email)
        if existing is not None:
            raise UserAlreadyExistsError(f"User with email {email} already exists")

        user = await self._repo.create(
            email=email, hashed_password=hash_password(password), full_name=full_name
        )
        await self._session.commit()
        return user
