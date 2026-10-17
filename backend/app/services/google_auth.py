"""Google でのログイン・新規の登録・既存のアカウントとの連携。

本人は `google_sub`(ID トークンの sub)で先に探す。Google 側のメールが後で変わっても、連携した
アカウントのまま入れるようにするため(アプリのメールは変えない)。sub で見つからないときだけメールで
探す。登録でメールを確かめないので、同じメールのアカウントには確かめずに紐付けない(409 を返し、
パスワードで確かめる連携へ進ませる)。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.errors import (
    GoogleAccountConflictError,
    GoogleEmailNotVerifiedError,
    GoogleLinkRequiredError,
    InvalidCredentialsError,
)
from app.services.google_identity import GoogleIdentity

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
            raise InvalidCredentialsError("Invalid email or password")
        _require_active(user)

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
        raise InvalidCredentialsError("User is inactive")
    return user
