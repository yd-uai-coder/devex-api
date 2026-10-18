import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.errors import (
    CurrentPasswordMismatchError,
    PasswordAlreadySetError,
    PasswordNotSetError,
    ReviewNotAllowedError,
    UserAlreadyExistsError,
)
from app.services.llm_resolver import LlmResolver


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
        """設計レビューの設定を本人が切り替える。使えないアカウント(superuser でなく、レビューの
        用途の AI の自分のキーも無い)は403(保存値を変えさせない ── app/review/policy.py)。"""
        if not await LlmResolver(self._session).review_allowed(user):
            raise ReviewNotAllowedError(
                "設計レビューは、マイページでレビューの AI の API キーを登録すると使えます。"
            )
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
        ほかのユーザーが使っているメールは 409。今と同じメールなら何もせずに返す。パスワードを
        持たない人は 409 PASSWORD_NOT_SET(先に設定させる)。"""
        require_password(user)
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
        呼び出し側(ルート)が AuthService で行う(このサービスは Redis を持たない)。パスワードを
        持たない人は 409 PASSWORD_NOT_SET(「設定」を使わせる)。"""
        require_password(user)
        self._verify_current_password(user, current_password)
        user.hashed_password = hash_password(new_password)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def set_password(self, user: User, *, new_password: str) -> User:
        """パスワードを持たない(Google だけの)利用者に、今のパスワードを求めずにパスワードを
        設定する。持つ人は 409 PASSWORD_ALREADY_SET(今のパスワードで確かめる「変更」を使わせる)。
        refresh の失効と出し直しは、変更と同じく呼び出し側が行う。"""
        if user.hashed_password is not None:
            raise PasswordAlreadySetError("パスワードは設定済みです。")
        user.hashed_password = hash_password(new_password)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    @staticmethod
    def _verify_current_password(user: User, current_password: str) -> None:
        # パスワードを持たない(Google だけの)利用者は、何を入れても合わない
        if user.hashed_password is None or not verify_password(
            current_password, user.hashed_password
        ):
            raise CurrentPasswordMismatchError("今のパスワードが正しくありません。")


def require_password(user: User) -> None:
    """パスワードを持たない(Google だけの)利用者を 409 PASSWORD_NOT_SET で断る。今のパスワードが
    要る操作と、連携の解除(app/services/google_auth.py)で共有する。"""
    if user.hashed_password is None:
        raise PasswordNotSetError("先にパスワードを設定してください。")
