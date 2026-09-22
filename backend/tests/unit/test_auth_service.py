import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.services.auth import AuthService
from app.services.errors import InvalidCredentialsError, InvalidTokenError
from app.services.user import UserService


class FakeRedis:
    """AuthServiceが使うredis.asyncio.Redisの一部機能だけを模した、テスト用のインメモリ代替実装。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        """キーと値をインメモリ辞書に保存する（TTLは無視する）。"""
        self._store[key] = value

    async def exists(self, key: str) -> int:
        """キーが存在すれば1、存在しなければ0を返す（redisのexistsを模す）。"""
        return int(key in self._store)

    async def delete(self, key: str) -> int:
        """キーを削除する。削除できた場合は1、存在しなかった場合は0を返す。"""
        return int(self._store.pop(key, None) is not None)


@pytest.fixture
def fake_redis() -> FakeRedis:
    """各テストに独立したFakeRedisインスタンスを渡すフィクスチャ。"""
    return FakeRedis()


async def _create_user(session: AsyncSession):
    """テスト用に固定のメールアドレス・パスワードでユーザーを1件作成するヘルパー。"""
    return await UserService(session).create_user(email="dave@example.com", password="s3cret")


async def test_authenticate_succeeds_with_correct_password(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> None:
    await _create_user(db_session)
    auth_service = AuthService(db_session, fake_redis)

    user = await auth_service.authenticate(email="dave@example.com", password="s3cret")

    assert user.email == "dave@example.com"


async def test_authenticate_rejects_wrong_password(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> None:
    await _create_user(db_session)
    auth_service = AuthService(db_session, fake_redis)

    with pytest.raises(InvalidCredentialsError):
        await auth_service.authenticate(email="dave@example.com", password="wrong")


async def test_authenticate_rejects_unknown_email(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> None:
    auth_service = AuthService(db_session, fake_redis)

    with pytest.raises(InvalidCredentialsError):
        await auth_service.authenticate(email="nobody@example.com", password="whatever")


async def test_issue_tokens_stores_refresh_token_in_redis(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> None:
    user = await _create_user(db_session)
    auth_service = AuthService(db_session, fake_redis)

    access_token, refresh_token = await auth_service.issue_tokens(user)

    claims = decode_token(refresh_token)
    assert fake_redis._store[f"refresh_token:{user.id}:{claims['jti']}"] == "1"
    assert decode_token(access_token)["sub"] == str(user.id)


async def test_refresh_access_token_succeeds_for_valid_refresh_token(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> None:
    user = await _create_user(db_session)
    auth_service = AuthService(db_session, fake_redis)
    _, refresh_token = await auth_service.issue_tokens(user)

    access_token = await auth_service.refresh_access_token(refresh_token)

    assert decode_token(access_token)["sub"] == str(user.id)


async def test_refresh_access_token_rejects_revoked_token(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> None:
    user = await _create_user(db_session)
    auth_service = AuthService(db_session, fake_redis)
    _, refresh_token = await auth_service.issue_tokens(user)
    await auth_service.revoke_refresh_token(refresh_token)

    with pytest.raises(InvalidTokenError):
        await auth_service.refresh_access_token(refresh_token)


async def test_refresh_access_token_rejects_garbage_token(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> None:
    auth_service = AuthService(db_session, fake_redis)

    with pytest.raises(InvalidTokenError):
        await auth_service.refresh_access_token("not-a-real-token")
