import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.errors import UserAlreadyExistsError
from app.services.user import UserService


async def test_create_user_hashes_password(db_session: AsyncSession) -> None:
    service = UserService(db_session)

    user = await service.create_user(email="alice@example.com", password="s3cret")

    assert user.email == "alice@example.com"
    assert user.hashed_password != "s3cret"


async def test_create_user_rejects_duplicate_email(db_session: AsyncSession) -> None:
    service = UserService(db_session)
    await service.create_user(email="bob@example.com", password="s3cret")

    with pytest.raises(UserAlreadyExistsError):
        await service.create_user(email="bob@example.com", password="other")


async def test_get_by_email_returns_none_when_missing(db_session: AsyncSession) -> None:
    service = UserService(db_session)

    user = await service.get_by_email("nobody@example.com")

    assert user is None


async def test_get_by_id_returns_created_user(db_session: AsyncSession) -> None:
    service = UserService(db_session)
    created = await service.create_user(email="carol@example.com", password="s3cret")

    fetched = await service.get_by_id(created.id)

    assert fetched is not None
    assert fetched.email == "carol@example.com"
