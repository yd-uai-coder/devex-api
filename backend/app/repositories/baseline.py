import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import defer, selectinload

from app.models.baseline import Baseline, BaselineItem
from app.repositories.base import CRUDRepository


@dataclass(frozen=True)
class ItemSnapshot:
    """束に写す1件(文書1つ、または段階1つ)。列の意味は BaselineItem を参照。"""

    key: str
    included: bool
    state: str
    source_version: int | None = None
    content: str | None = None
    model: dict | None = None
    generated_model: str | None = None


class BaselineRepository(CRUDRepository[Baseline]):
    """合意済みの版(束)と写しの永続化。写しは束と一緒に読む(selectinload)。束は作った後に
    書き換えないので、作成と読み出しだけを持つ。"""

    model = Baseline

    def _default_options(self) -> tuple[Any, ...]:
        return (selectinload(Baseline.items),)

    async def next_number(self, project_id: uuid.UUID) -> int:
        """プロジェクトの次の番号(最初は1)。"""
        stmt = select(func.max(Baseline.number)).where(Baseline.project_id == project_id)
        current = (await self._session.execute(stmt)).scalar_one_or_none()
        return (current or 0) + 1

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        number: int,
        agreed_at: datetime,
        reason: str,
        items: Sequence[ItemSnapshot],
        counterpart: str | None = None,
        memo: str = "",
        recorded_by: uuid.UUID | None = None,
    ) -> Baseline:
        """束と写しを作る。写しは渡した順に並ぶ。flushまで行う。記録した日時はアプリで入れる
        (作った束をそのまま返すとき、読み直さずに日時を出せるように)。"""
        baseline = Baseline(
            project_id=project_id,
            number=number,
            counterpart=counterpart,
            agreed_at=agreed_at,
            memo=memo,
            reason=reason,
            recorded_by=recorded_by,
            created_at=datetime.now(UTC),
            items=[
                BaselineItem(
                    position=position,
                    key=item.key,
                    included=item.included,
                    state=item.state,
                    source_version=item.source_version,
                    content=item.content,
                    model=item.model,
                    generated_model=item.generated_model,
                )
                for position, item in enumerate(items)
            ],
        )
        self._session.add(baseline)
        await self._session.flush()
        return baseline

    async def list_for_project(self, project_id: uuid.UUID) -> list[Baseline]:
        """プロジェクトの束を新しい順に返す。写しは付けるが、中身(`content`・`model`)は読まない
        (一覧の応答は写しの中身を返さず、束が増えるほど重くなるため)。"""
        stmt = (
            select(Baseline)
            .where(Baseline.project_id == project_id)
            .order_by(Baseline.number.desc())
            .options(
                selectinload(Baseline.items).options(
                    defer(BaselineItem.content), defer(BaselineItem.model)
                )
            )
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def numbers(self, project_id: uuid.UUID) -> list[int]:
        """プロジェクトの束の番号(古い順)。"""
        stmt = (
            select(Baseline.number)
            .where(Baseline.project_id == project_id)
            .order_by(Baseline.number)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def latest(self, project_id: uuid.UUID) -> Baseline | None:
        """プロジェクトの最新の束(写し付き。中身も読む)。無ければ None。"""
        stmt = (
            select(Baseline)
            .where(Baseline.project_id == project_id)
            .order_by(Baseline.number.desc())
            .limit(1)
            .options(*self._default_options())
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def get_by_number(self, *, project_id: uuid.UUID, number: int) -> Baseline | None:
        """プロジェクトの番号の束(写し付き)。無ければ None。"""
        return await self.find_one(project_id=project_id, number=number)
