from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import Base, engine
from app.infrastructure.redis import get_redis_pool
from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    """FastAPI -> PostgreSQL -> Redisの実スタックに疎通するテスト用HTTPクライアントを提供する。

    DATABASE_URL / REDIS_URLが実サービス（例：`docker compose up postgres redis`で
    起動したもの）を指している必要がある。

    このフィクスチャは毎回`Base.metadata.create_all`/`drop_all`でテーブルを作り直すため、
    開発用DBと同じDBに向けて実行すると開発中のデータ・スキーマを消してしまう。
    必ず`docker-compose.test.yml`で`DATABASE_URL`をテスト専用DBへ差し替えて実行すること
    （`devex-api/CLAUDE.md`「テストの分離」節参照）:

        docker compose -f docker-compose.yml -f docker-compose.test.yml \\
          run --rm --no-deps backend uv run pytest -m integration tests/integration
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

    # 次のテスト前に後始末をする
    await engine.dispose()
    await get_redis_pool().disconnect()
    get_redis_pool.cache_clear()
