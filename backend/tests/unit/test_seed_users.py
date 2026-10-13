from scripts.seed import SEED_SUPERUSER, SEED_USER, seed_users
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.user import UserRepository


async def test_seed_users_creates_user_and_superuser(db_session: AsyncSession) -> None:
    await seed_users(db_session)

    repo = UserRepository(db_session)
    user = await repo.get_by_email(SEED_USER["email"])
    superuser = await repo.get_by_email(SEED_SUPERUSER["email"])

    assert user is not None and user.is_superuser is False
    assert superuser is not None and superuser.is_superuser is True


async def test_seed_users_is_idempotent(db_session: AsyncSession) -> None:
    await seed_users(db_session)
    await seed_users(db_session)

    count = await db_session.scalar(select(func.count()).select_from(User))

    assert count == 2
