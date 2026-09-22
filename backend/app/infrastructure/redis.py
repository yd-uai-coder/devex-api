from collections.abc import AsyncGenerator
from functools import lru_cache

from redis.asyncio import ConnectionPool, Redis

from app.core.config import settings


@lru_cache
def get_redis_pool() -> ConnectionPool:
    """Redis接続プールをプロセス内で1つだけ生成し、以後は使い回す。"""
    return ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)


def get_redis_client() -> Redis:
    """共有の接続プールに紐づいたRedisクライアントを新規生成する。"""
    return Redis(connection_pool=get_redis_pool())


async def get_redis() -> AsyncGenerator[Redis]:
    """共有プールに紐づいたRedisクライアントを生成するFastAPI依存関数。"""
    client = get_redis_client()
    try:
        yield client
    finally:
        await client.aclose()
