from dataclasses import dataclass

from redis.asyncio import Redis

from app.services.errors import RateLimitExceededError


@dataclass(frozen=True)
class RateLimit:
    """1つの制限ウィンドウ（秒数と、その間に許可する最大リクエスト数）を表す設定値。"""

    window_seconds: int
    max_requests: int


class RateLimiter:
    """Redisのカウンタを用いて、任意のリソース単位・複数ウィンドウでレート制限を課す汎用クラス。"""

    def __init__(self, redis: Redis, *, resource: str, limits: list[RateLimit]) -> None:
        # redis: カウンタの保存に使う非同期Redisクライアント
        # resource: 制限対象を識別する名前（例: "chat_message"）
        # limits: 同時に適用する制限ウィンドウのリスト
        self._redis = redis
        self._resource = resource
        self._limits = limits

    async def enforce(self, identifier: str) -> None:
        """identifier単位でカウンタを加算し、いずれかの制限を超えていればRateLimitExceededErrorを送出する。"""
        # limit: 設定済みの制限ウィンドウを1つずつ順番にチェックする
        for limit in self._limits:
            key = self._build_key(identifier, limit.window_seconds)
            # count: このウィンドウ内でのインクリメント後のリクエスト回数。
            # incr と expire を MULTI/EXEC で 1 往復にまとめる ── 別々に送ると incr 直後に
            # プロセスが落ちた場合に TTL 無しのキーが残り、その利用者が永久に制限される。
            # expire の NX は「TTL 未設定のときだけ設定」(ウィンドウは最初のアクセスで開始し、
            # 以後のアクセスで延長しない)。Redis 7.0 以降が必要。
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, limit.window_seconds, nx=True)
                count, _ = await pipe.execute()
            if count > limit.max_requests:
                raise RateLimitExceededError(
                    f"Rate limit exceeded for {self._resource} "
                    f"({limit.max_requests} per {limit.window_seconds}s)"
                )

    def _build_key(self, identifier: str, window_seconds: int) -> str:
        """Redisキーを resource:window_seconds:identifier の形式で組み立てる。"""
        return f"rate_limit:{self._resource}:{window_seconds}:{identifier}"
