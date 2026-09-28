import os

# 重要：ここでの環境変数設定は、app.core.databaseなど設定値を読むモジュールがimportされるより
# 前に実行する必要がある。Pythonのimportは一度実行されるとキャッシュされるため、
# 先に本物の設定でモジュールがimportされてしまうと、後から上書きしても手遅れになる。
# この順序を誤ると、テストが誤って開発/本番用のDBに接続してしまう事故につながる。
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://devex-user:devex-db-pg-0123@localhost:5432/test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-api-key")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily-api-key")
os.environ.setdefault("ENVIRONMENT", "test")

from collections.abc import AsyncGenerator, Generator  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
import structlog  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.database import Base  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_structlog_state() -> Generator[None]:
    """`app.main`のimport(`test_cors.py`等)は`configure_logging()`を実行し、structlogの
    グローバル設定を書き換える。この変更はプロセス全体に及び、他のテストファイルの
    `structlog.testing.capture_logs`(DEBUGログのフィルタしきい値など)に影響しうるため、
    各テストの前後で必ずライブラリ既定値へ戻す。"""
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()

@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """リポジトリ/サービスのユニットテスト用に、インメモリSQLiteの非同期セッションを提供する。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # テスト用DBにモデル定義から全テーブルを作成する
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()
