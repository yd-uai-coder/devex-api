"""Google でのログイン・新規の登録・既存のアカウントとの連携。

本人は `google_sub`(ID トークンの sub)で先に探す。Google 側のメールが後で変わっても、連携した
アカウントのまま入れるようにするため(アプリのメールは変えない)。sub で見つからないときだけメールで
探す。登録でメールを確かめないので、同じメールのアカウントには確かめずに紐付けない(409 を返し、
パスワードで確かめる連携へ進ませる)。マイページからの連携は、ログイン中の本人が今のパスワードで
確かめるので、メールが違う Google も紐付けられる。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.errors import (
    INACTIVE_USER_MESSAGE,
    INVALID_CREDENTIALS_MESSAGE,
    CurrentPasswordMismatchError,
    GoogleAccountConflictError,
    GoogleEmailNotVerifiedError,
    GoogleLinkRequiredError,
    InvalidCredentialsError,
)
from app.services.google_identity import GoogleIdentity
from app.services.user import require_password

# users.user_name の列の長さ
_USER_NAME_MAX_LENGTH = 255


class GoogleAuthService:
    """検証済みの Google の本人の値から、ログインする利用者を決める。トークンの発行は呼び出し側
    (ルート)が AuthService で行う。"""

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._users = UserRepository(session)

    async def login_or_register(self, identity: GoogleIdentity) -> User:
        """連携済みならその人、だれのメールでもなければ新しく登録した人を返す。同じメールの
        アカウントがあれば、連携していなければ 409 GOOGLE_LINK_REQUIRED、別の Google と連携済み
        なら 409 GOOGLE_ACCOUNT_CONFLICT。"""
        _require_verified_email(identity)
        user = await self._users.get_by_google_sub(identity.sub)
        if user is not None:
            return _require_active(user)

        existing = await self._users.get_by_email(identity.email)
        if existing is not None:
            if existing.google_sub is not None:
                raise GoogleAccountConflictError(
                    "このメールアドレスのアカウントは、別の Google アカウントと連携済みです。"
                )
            raise GoogleLinkRequiredError(
                "このメールアドレスのアカウントがあります。"
                "パスワードを入れると Google と連携します。"
            )

        name = identity.name[:_USER_NAME_MAX_LENGTH] if identity.name else None
        user = await self._users.create(
            email=identity.email, hashed_password=None, user_name=name, google_sub=identity.sub
        )
        await self._session.commit()
        return user

    async def link(self, identity: GoogleIdentity, *, password: str) -> User:
        """同じメールのアカウントのパスワードで本人を確かめ、Google と連携して、その人を返す。
        メールのアカウントが無い・パスワードが違う・パスワードを持たない・無効な人は、ログインと
        同じ 401(見分けさせない)。"""
        _require_verified_email(identity)
        user = await self._users.get_by_email(identity.email)
        if (
            user is None
            or user.hashed_password is None
            or not verify_password(password, user.hashed_password)
        ):
            raise InvalidCredentialsError(INVALID_CREDENTIALS_MESSAGE)
        _require_active(user)

        return await self._attach(user, identity)

    async def link_current(
        self, user: User, identity: GoogleIdentity, *, current_password: str
    ) -> User:
        """マイページから、ログイン中の本人を Google と連携する。メールの一致は求めない。今の
        パスワードで確かめる(盗まれたセッションで他人の Google を足させない。不一致は 400、
        ログイン中の操作なので 401 にしない)。パスワードを持たない人は 409 PASSWORD_NOT_SET。"""
        _require_verified_email(identity)
        require_password(user)
        if user.hashed_password is None or not verify_password(
            current_password, user.hashed_password
        ):
            raise CurrentPasswordMismatchError("今のパスワードが正しくありません。")
        return await self._attach(user, identity)

    async def unlink(self, user: User) -> User:
        """Google との連携を解く。パスワードを持たない人は 409 PASSWORD_NOT_SET(どちらでも
        ログインできなくなるため)。連携していなくても成功。refresh の失効と出し直しは呼び出し側
        (ルート)が AuthService で行う。"""
        require_password(user)
        if user.google_sub is None:
            return user
        user.google_sub = None
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def _attach(self, user: User, identity: GoogleIdentity) -> User:
        """本人の確かめが済んだ利用者に google_sub を記録する(ログインの連携とマイページで共有)。
        同じ Google でのやり直しは成功。その人が別の Google と、またはその Google がほかの人と
        連携済みなら 409 GOOGLE_ACCOUNT_CONFLICT。"""
        if user.google_sub == identity.sub:
            return user
        linked = await self._users.get_by_google_sub(identity.sub)
        if user.google_sub is not None or linked is not None:
            raise GoogleAccountConflictError(
                "このアカウントか Google アカウントが、ほかと連携済みです。"
            )
        user.google_sub = identity.sub
        await self._session.commit()
        await self._session.refresh(user)
        return user


def _require_verified_email(identity: GoogleIdentity) -> None:
    if not identity.email_verified:
        raise GoogleEmailNotVerifiedError(
            "Google アカウントのメールアドレスが確認されていません。"
        )


def _require_active(user: User) -> User:
    if not user.is_active:
        raise InvalidCredentialsError(INACTIVE_USER_MESSAGE)
    return user
