"""設計書の生成の進み具合(どの文書を作り直していて、どこまでできたか)を Redis に置く。

生成はバックグラウンドで上流から1文書ずつ作り、保存は最後にまとめて行う(失敗したら全部取り消す)。
そのため途中の様子は DB からは見えない。画面に「要件定義：生成完了 外部設計：生成中 …」を出すため、
生成の間だけ Redis に `{targets, done, current}` を書く。終わったら(成功・失敗とも)消す。
"""

import json
import uuid
from dataclasses import asdict, dataclass, field

from redis.asyncio import Redis

# 生成が止まってキーが残っても、いつまでも「生成中」と出さない長さ(生成の回収の15分より長く取る)
_TTL_SECONDS = 3600


@dataclass
class GenerationProgress:
    """作り直す文書(上流から)・できた文書・今作っている文書。"""

    targets: list[str]
    done: list[str] = field(default_factory=list)
    current: str | None = None


def _key(project_id: uuid.UUID) -> str:
    return f"doc_generation:{project_id}"


class GenerationProgressStore:
    """進み具合の読み書き。Redis を渡さない呼び出し(テストの多く)では何もしない。"""

    def __init__(self, redis: Redis | None) -> None:
        self._redis = redis

    async def write(self, project_id: uuid.UUID, progress: GenerationProgress) -> None:
        if self._redis is None:
            return
        await self._redis.set(_key(project_id), json.dumps(asdict(progress)), ex=_TTL_SECONDS)

    async def read(self, project_id: uuid.UUID) -> GenerationProgress | None:
        if self._redis is None:
            return None
        raw = await self._redis.get(_key(project_id))
        if raw is None:
            return None
        return GenerationProgress(**json.loads(raw))

    async def clear(self, project_id: uuid.UUID) -> None:
        if self._redis is None:
            return
        await self._redis.delete(_key(project_id))
