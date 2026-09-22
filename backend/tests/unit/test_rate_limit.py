"""`RateLimiter`(Redis ベースの汎用レート制限)。

テスト対象 / ドライバ / スタブ:
- 対象: `app.services.rate_limit.RateLimiter`
- ドライバ: このテスト関数
- スタブ: `tests.fixtures.fake_redis.FakeRedis`(incr / expire / pipeline)
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from redis.asyncio import Redis
from tests.fixtures.fake_redis import FakeRedis

from app.services.errors import RateLimitExceededError
from app.services.rate_limit import RateLimit, RateLimiter


async def test_rate_limiter_uses_a_single_transaction_for_incr_and_expire() -> None:
    """incr と expire を別々に送ると、間でプロセスが落ちたとき TTL 無しキーが残る(永久ロック)。
    pipeline(MULTI/EXEC)で 1 往復にまとめ、expire は NX(TTL 未設定のときだけ)で送る。"""

    class _Spy(FakeRedis):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[tuple[str, dict[str, Any]]] = []

        async def incr(self, key: str) -> int:
            self.commands.append(("incr", {}))
            return await super().incr(key)

        async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
            self.commands.append(("expire", {"nx": nx}))
            return await super().expire(key, seconds, nx=nx)

    redis = _Spy()
    limiter = RateLimiter(
        cast(Redis, redis), resource="t", limits=[RateLimit(window_seconds=60, max_requests=5)]
    )
    await limiter.enforce("u1")

    assert redis.commands == [("incr", {}), ("expire", {"nx": True})]


async def test_rate_limiter_blocks_after_max_requests() -> None:
    limiter = RateLimiter(
        cast(Redis, FakeRedis()),
        resource="t",
        limits=[RateLimit(window_seconds=60, max_requests=2)],
    )
    await limiter.enforce("u1")
    await limiter.enforce("u1")
    with pytest.raises(RateLimitExceededError):
        await limiter.enforce("u1")
