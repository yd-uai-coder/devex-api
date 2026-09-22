from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import Base, engine
from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    """FastAPI -> PostgreSQL -> Redisの実スタックに疎通するテスト用HTTPクライアントを提供する。

    DATABASE_URL / REDIS_URLが実サービス（例：`docker compose up postgres redis`で
    起動したもの）を指している必要がある。
    """
    async with engine.begin() as conn:
        # テスト用DBに毎回まっさらな状態でテーブルを作り直す
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    async with engine.begin() as conn:
        # テスト終了後にテーブルを破棄し、次のテストに影響を残さないようにする
        await conn.run_sync(Base.metadata.drop_all)
