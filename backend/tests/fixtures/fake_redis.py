"""レート制限テスト用の最小 FakeRedis。RateLimiter が使う incr / expire / pipeline / get / set
だけを実装する(既存 tests/unit/test_auth_service.py の FakeRedis(set/exists/delete)とは別物)。
"""

from __future__ import annotations


class _FakePipeline:
    """`redis.asyncio` の Pipeline の代替。コマンドは積むだけで、execute で順に実行する。"""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queue: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def incr(self, key: str) -> _FakePipeline:
        self._queue.append(("incr", (key,), {}))
        return self

    def expire(self, key: str, seconds: int, nx: bool = False) -> _FakePipeline:
        self._queue.append(("expire", (key, seconds), {"nx": nx}))
        return self

    async def execute(self) -> list[object]:
        results: list[object] = []
        for name, args, kwargs in self._queue:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        self._queue.clear()
        return results


class FakeRedis:
    """redis.asyncio.Redis の incr / expire / pipeline / get / set だけを模したインメモリ実装。"""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._values: dict[str, str] = {}

    async def incr(self, key: str) -> int:
        """key のカウンタを 1 増やして増加後の値を返す。"""
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(
        self,
        key: str,  # noqa: ARG002
        seconds: int,  # noqa: ARG002
        nx: bool = False,  # noqa: ARG002
    ) -> bool:
        """TTL 設定。テストでは寿命を管理しないので何もしない。"""
        return True

    def pipeline(self, transaction: bool = True) -> _FakePipeline:  # noqa: ARG002
        """MULTI/EXEC の代わり。積んだ incr / expire を execute でまとめて実行する。"""
        return _FakePipeline(self)

    async def get(self, key: str) -> str | None:
        """key の値を返す(無ければ None)。"""
        return self._values.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:  # noqa: ARG002
        """key に値を設定する。`ex`(TTL秒)はテストでは失効させないので記録しない。"""
        self._values[key] = value
        return True
