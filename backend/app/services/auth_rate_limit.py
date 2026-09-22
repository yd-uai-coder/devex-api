"""認証エンドポイント(login / register)のレート制限。

他の全エンドポイントは利用者 id をキーに制限しているが、認証前は id が無いので
IP(+ ログインはメール)をキーにする。ログインは「IP 単位」(1 つの IP からの総当たり)と
「メール単位」(分散した IP からの 1 アカウントへの総当たり)の両方を課す。
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings
from app.services.rate_limit import RateLimit, RateLimiter

_HOUR = 3600


class AuthRateLimiter:
    """login / register の試行回数を Redis で数え、超過なら RateLimitExceededError を送出する。"""

    def __init__(self, redis: Redis) -> None:
        # redis: カウンタ保存用の非同期 Redis クライアント
        self._login_ip = RateLimiter(
            redis,
            resource="login_ip",
            limits=[RateLimit(_HOUR, settings.LOGIN_RATE_LIMIT_PER_IP_PER_HOUR)],
        )
        self._login_email = RateLimiter(
            redis,
            resource="login_email",
            limits=[RateLimit(_HOUR, settings.LOGIN_RATE_LIMIT_PER_EMAIL_PER_HOUR)],
        )
        self._register_ip = RateLimiter(
            redis,
            resource="register_ip",
            limits=[RateLimit(_HOUR, settings.REGISTER_RATE_LIMIT_PER_IP_PER_HOUR)],
        )

    async def enforce_login(self, *, ip: str, email: str) -> None:
        """ログイン試行 1 回分を数える。成功・失敗を問わず数える(失敗だけ数えると、
        成功するまで何度でも試せる攻撃側に有利になり、判定も後回しになる)。"""
        await self._login_ip.enforce(ip)
        # メールは大文字小文字を区別せず数える(表記ゆれで制限を回避させない)
        await self._login_email.enforce(email.strip().lower())

    async def enforce_register(self, *, ip: str) -> None:
        """アカウント登録試行 1 回分を数える。"""
        await self._register_ip.enforce(ip)
