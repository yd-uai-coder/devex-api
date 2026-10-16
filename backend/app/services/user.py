import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.errors import (
    CurrentPasswordMismatchError,
    ReviewNotAllowedError,
    UserAlreadyExistsError,
)


class UserService:
    """ユーザーの参照・新規作成・アカウントの変更に関するユースケースを担当するサービス。"""

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

    async def create_user(self, *, email: str, password: str, user_name: str | None = None) -> User:
        """メールアドレスの重複が無いことを確認し、パスワードをハッシュ化して新規ユーザーを登録する。"""
        existing = await self._repo.get_by_email(email)
        if existing is not None:
            raise UserAlreadyExistsError(f"User with email {email} already exists")

        user = await self._repo.create(
            email=email, hashed_password=hash_password(password), user_name=user_name
        )
        await self._session.commit()
        return user

    async def update_review_setting(self, user: User, *, enabled: bool) -> User:
        """設計レビューの設定を本人が切り替える。superuser 以外は403(保存値を変えさせない。
        実効値は superuser でなければ常にオフ ── app/review/policy.py)。"""
        if not user.is_superuser:
            raise ReviewNotAllowedError("設計レビューの設定は管理者だけが変えられます。")
        user.review_enabled = enabled
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def update_user_name(self, user: User, *, user_name: str) -> User:
        """ユーザーネームを変える(本人の操作。確かめは要らない)。"""
        user.user_name = user_name
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def change_email(self, user: User, *, new_email: str, current_password: str) -> User:
        """今のパスワードで本人を確かめてから、メールアドレスを即時に変える(確認メールは送らない)。
        ほかのユーザーが使っているメールは 409。今と同じメールなら何もせずに返す。"""
        self._verify_current_password(user, current_password)
        if new_email == user.email:
            return user
        if await self._repo.get_by_email(new_email) is not None:
            raise UserAlreadyExistsError(f"User with email {new_email} already exists")
        user.email = new_email
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def change_password(
        self, user: User, *, current_password: str, new_password: str
    ) -> User:
        """今のパスワードで本人を確かめてから、パスワードを変える。refresh の失効と出し直しは
        呼び出し側(ルート)が AuthService で行う(このサービスは Redis を持たない)。"""
        self._verify_current_password(user, current_password)
        user.hashed_password = hash_password(new_password)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    @staticmethod
    def _verify_current_password(user: User, current_password: str) -> None:
        if not verify_password(current_password, user.hashed_password):
            raise CurrentPasswordMismatchError("今のパスワードが正しくありません。")
