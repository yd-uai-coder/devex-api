"""CR の共有リンクの公開の入口(ログインの要らない閲覧)のレート制限。

認証の入口(auth_rate_limit.py)と同じく、利用者 id が無いので IP をキーにする。トークンは推測
できない長さだが、総当たりと読み出しの繰り返しを止めるため、見つからない・期限切れのときも数える
(リンクを引く前に数える)。
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings
from app.services.rate_limit import RateLimit, RateLimiter

_HOUR = 3600


class ShareRateLimiter:
    """共有リンクの閲覧の回数を Redis で数え、超過なら RateLimitExceededError を送出する。"""

    def __init__(self, redis: Redis) -> None:
        # redis: カウンタ保存用の非同期 Redis クライアント
        self._view_ip = RateLimiter(
            redis,
            resource="share_view_ip",
            limits=[RateLimit(_HOUR, settings.SHARE_RATE_LIMIT_PER_IP_PER_HOUR)],
        )

    async def enforce_view(self, *, ip: str) -> None:
        """閲覧1回分を数える。"""
        await self._view_ip.enforce(ip)
