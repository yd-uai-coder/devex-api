"""表示名の列の改名(full_name → user_name)のテスト。

SUT: User モデル・UserRead・UserService.create_user・登録のルート(auth.register)・seed の利用者
ドライバ: pytest のテスト関数(ルートの関数を直接呼ぶ。既存のルートのテストと同じ形)
スタブ: FakeRedis(登録の回数の上限を数えるだけ)。DB はテスト用の SQLite のセッション
"""

from scripts.seed import SEED_USER, seed_users
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_redis import FakeRedis

from app.api.routes.auth import register
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserRead
from app.services.user import UserService


async def test_create_user_stores_user_name(db_session: AsyncSession) -> None:
    user = await UserService(db_session).create_user(
        email="erin@example.com", password="s3cret", user_name="Erin"
    )

    assert user.user_name == "Erin"
    assert UserRead.model_validate(user).user_name == "Erin"


async def test_register_route_takes_user_name(db_session: AsyncSession) -> None:
    payload = UserCreate(email="fay@example.com", password="s3cret", user_name="Fay")

    read = await register(payload, db_session, FakeRedis(), "203.0.113.1")  # type: ignore[arg-type]

    assert read.user_name == "Fay"
    assert "full_name" not in read.model_dump()


async def test_seed_user_has_user_name(db_session: AsyncSession) -> None:
    await seed_users(db_session)

    user = await UserRepository(db_session).get_by_email(SEED_USER["email"])

    assert user is not None
    assert user.user_name == SEED_USER["user_name"]
