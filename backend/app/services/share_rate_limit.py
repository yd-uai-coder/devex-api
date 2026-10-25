"""CR の共有リンクの公開の入口(ログインの要らない閲覧と、コメント・合意 / 却下の書き込み)の
レート制限。

認証の入口(auth_rate_limit.py)と同じく、利用者 id が無いので IP をキーにする。トークンは推測
できない長さだが、総当たりと読み出しの繰り返しを止めるため、見つからない・期限切れのときも数える
(リンクを引く前に数える)。書き込みは閲覧と別の、より少ない上限で数える(閲覧の回数を書き込みで
使い切らないように。書き込みは閲覧の上限にも数えない)。
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings
from app.services.rate_limit import RateLimit, RateLimiter

_HOUR = 3600


class ShareRateLimiter:
    """共有リンクの閲覧・書き込みの回数を Redis で数え、超過なら RateLimitExceededError を
    送出する。"""

    def __init__(self, redis: Redis) -> None:
        # redis: カウンタ保存用の非同期 Redis クライアント
        self._view_ip = RateLimiter(
            redis,
            resource="share_view_ip",
            limits=[RateLimit(_HOUR, settings.SHARE_RATE_LIMIT_PER_IP_PER_HOUR)],
        )
        self._write_ip = RateLimiter(
            redis,
            resource="share_write_ip",
            limits=[RateLimit(_HOUR, settings.SHARE_WRITE_RATE_LIMIT_PER_IP_PER_HOUR)],
        )

    async def enforce_view(self, *, ip: str) -> None:
        """閲覧1回分を数える。"""
        await self._view_ip.enforce(ip)

    async def enforce_write(self, *, ip: str) -> None:
        """書き込み(コメント・合意 / 却下)1回分を数える。"""
        await self._write_ip.enforce(ip)
