"""`AuthRateLimiter`(login / register のレート制限)。

テスト対象 / ドライバ / スタブ:
- 対象: `app.services.auth_rate_limit.AuthRateLimiter`
- ドライバ: このテスト関数
- スタブ: `tests.fixtures.fake_redis.FakeRedis`(incr / expire だけを使う)
"""

from __future__ import annotations

from typing import cast

import pytest
from redis.asyncio import Redis
from tests.fixtures.fake_redis import FakeRedis

from app.core.config import settings
from app.services.auth_rate_limit import AuthRateLimiter
from app.services.errors import RateLimitExceededError


async def test_login_is_limited_per_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_PER_IP_PER_HOUR", 2)
    limiter = AuthRateLimiter(cast(Redis, FakeRedis()))

    await limiter.enforce_login(ip="1.1.1.1", email="a@example.com")
    await limiter.enforce_login(ip="1.1.1.1", email="b@example.com")
    with pytest.raises(RateLimitExceededError):
        await limiter.enforce_login(ip="1.1.1.1", email="c@example.com")
    # 別 IP は別枠
    await limiter.enforce_login(ip="2.2.2.2", email="a@example.com")


async def test_login_is_limited_per_email_case_insensitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IP を変えても、同じメール(大文字小文字違いを含む)への試行は数え続ける。"""
    monkeypatch.setattr(settings, "LOGIN_RATE_LIMIT_PER_EMAIL_PER_HOUR", 2)
    limiter = AuthRateLimiter(cast(Redis, FakeRedis()))

    await limiter.enforce_login(ip="1.1.1.1", email="Victim@Example.com")
    await limiter.enforce_login(ip="2.2.2.2", email="victim@example.com")
    with pytest.raises(RateLimitExceededError):
        await limiter.enforce_login(ip="3.3.3.3", email="  VICTIM@example.com ")


async def test_register_is_limited_per_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "REGISTER_RATE_LIMIT_PER_IP_PER_HOUR", 1)
    limiter = AuthRateLimiter(cast(Redis, FakeRedis()))

    await limiter.enforce_register(ip="1.1.1.1")
    with pytest.raises(RateLimitExceededError):
        await limiter.enforce_register(ip="1.1.1.1")
