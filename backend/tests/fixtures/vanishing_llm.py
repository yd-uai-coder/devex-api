"""バックグラウンドの処理の途中でプロジェクトが削除される場合を模す LLM のスタブ。

最初の LLM の呼び出しの前に、プロジェクトに削除の印を付けて commit する(別のリクエストの
DELETE の代わり。インメモリの SQLite は1つの接続なので、処理と同じセッションで付ける)。
`fail` を渡すと、印を付けた後のすべての呼び出しが失敗する(失敗の経路を通すため)。
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project


class _Hooked:
    """構造化出力のサブクライアントの前に、印を付ける処理を挟む。"""

    def __init__(self, inner: Any, before: Callable[[], Awaitable[None]]) -> None:
        self._inner = inner
        self._before = before

    async def ainvoke(self, messages: Any) -> Any:
        await self._before()
        return await self._inner.ainvoke(messages)


class VanishingLLM:
    """`inner`(FakeLLM)に委ねる前に、プロジェクトに削除の印を付ける LLM のスタブ。"""

    def __init__(
        self,
        inner: Any,
        session: AsyncSession,
        project_id: uuid.UUID,
        *,
        fail: Exception | None = None,
    ) -> None:
        self._inner = inner
        self._session = session
        self._project_id = project_id
        self._fail = fail
        self.marked = False

    async def _before_call(self) -> None:
        if not self.marked:
            await self._session.execute(
                update(Project)
                .where(Project.id == self._project_id)
                .values(deleted_at=datetime.now(UTC))
            )
            await self._session.commit()
            self.marked = True
        if self._fail is not None:
            raise self._fail

    def with_structured_output(self, schema: Any, **kwargs: Any) -> _Hooked:
        return _Hooked(self._inner.with_structured_output(schema, **kwargs), self._before_call)

    async def ainvoke(self, messages: Any) -> Any:
        await self._before_call()
        return await self._inner.ainvoke(messages)
