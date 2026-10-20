import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.agreements import AgreedItem, AgreementOrigin
from app.models.agreement import AgreementRecord
from app.repositories.base import CRUDRepository


class AgreementRepository(CRUDRepository[AgreementRecord]):
    """合意の記録の永続化。追記と読み出しだけを持つ(記録は直さず消さない)。出どころの束・CR は
    一緒に読む(selectinload。番号を出すため)。"""

    model = AgreementRecord

    def _default_options(self) -> tuple[Any, ...]:
        return (
            selectinload(AgreementRecord.baseline),
            selectinload(AgreementRecord.change_request),
        )

    async def append(
        self,
        *,
        project_id: uuid.UUID,
        items: Sequence[AgreedItem],
        origin: AgreementOrigin,
        removed: Sequence[AgreementRecord] = (),
        baseline_id: uuid.UUID | None = None,
        change_request_id: uuid.UUID | None = None,
        recorded_by: uuid.UUID | None = None,
    ) -> list[AgreementRecord]:
        """1回の合意の記録を追記する(要素の順 → 外れた要素の順に連番)。`removed`は今の合意の
        記録のうち外れたもので、キー・種類・表示名を写し、中身を NULL にする。flushまで行う。
        記録した日時はアプリで入れる(1回の記録の行は同じ日時)。"""
        seq = await self._next_seq(project_id)
        now = datetime.now(UTC)
        common = {
            "project_id": project_id,
            "origin": origin,
            "baseline_id": baseline_id,
            "change_request_id": change_request_id,
            "recorded_by": recorded_by,
            "created_at": now,
        }
        records = [
            AgreementRecord(
                seq=seq + i,
                key=item.key,
                kind=item.kind,
                label=item.label,
                content=item.content,
                **common,
            )
            for i, item in enumerate(items)
        ]
        records += [
            AgreementRecord(
                seq=seq + len(items) + i,
                key=record.key,
                kind=record.kind,
                label=record.label,
                content=None,
                **common,
            )
            for i, record in enumerate(removed)
        ]
        self._session.add_all(records)
        await self._session.flush()
        return records

    async def list_for_project(self, project_id: uuid.UUID) -> list[AgreementRecord]:
        """プロジェクトの記録を、記録の順(古い順)に返す。"""
        stmt = (
            select(AgreementRecord)
            .where(AgreementRecord.project_id == project_id)
            .order_by(AgreementRecord.seq)
            .options(*self._default_options())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def current(self, project_id: uuid.UUID) -> list[AgreementRecord]:
        """今の合意(キーごとの最新の記録。最新が「外れた」のキーは除く)を、記録の順に返す。
        記録の数はプロジェクトの要素の数 × 合意の回数ほどで小さいので、全部読んで畳む。"""
        latest: dict[str, AgreementRecord] = {}
        for record in await self.list_for_project(project_id):
            latest[record.key] = record
        return sorted(
            (r for r in latest.values() if r.content is not None), key=lambda r: r.seq
        )

    async def _next_seq(self, project_id: uuid.UUID) -> int:
        stmt = select(func.max(AgreementRecord.seq)).where(
            AgreementRecord.project_id == project_id
        )
        current = (await self._session.execute(stmt)).scalar_one_or_none()
        return (current or 0) + 1

