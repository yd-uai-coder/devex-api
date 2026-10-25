import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.confirmations import ConfirmationItem, ConfirmationStatus, Opportunity
from app.models.confirmation import ConfirmationItemRecord
from app.repositories.base import CRUDRepository

# 部分更新で「渡さなかった」を表す(決める機会の NULL〈未設定に戻す〉と区別するため)
UNSET: object = object()


class ConfirmationRepository(CRUDRepository[ConfirmationItemRecord]):
    """確認事項の利用者が決めた欄の永続化。項目のキーごとに1行で、上書きする。"""

    model = ConfirmationItemRecord

    async def list_for_project(self, project_id: uuid.UUID) -> list[ConfirmationItemRecord]:
        """プロジェクトの行を、作った順に返す。"""
        stmt = (
            select(ConfirmationItemRecord)
            .where(ConfirmationItemRecord.project_id == project_id)
            .order_by(ConfirmationItemRecord.created_at, ConfirmationItemRecord.key)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def save(
        self,
        project_id: uuid.UUID,
        item: ConfirmationItem,
        *,
        opportunity: Opportunity | None | object = UNSET,
        status: ConfirmationStatus | object = UNSET,
        memo: str | object = UNSET,
        updated_by: uuid.UUID | None = None,
    ) -> ConfirmationItemRecord:
        """項目の欄を保存する(行が無ければ作る)。渡した欄だけを変え、表示名は今の項目で
        書き直す。状態を渡したときは、CR で戻した印を消す(利用者が決め直したため)。flushまで
        行う。"""
        record = await self.find_one(project_id=project_id, key=item.key)
        if record is None:
            record = ConfirmationItemRecord(
                project_id=project_id,
                key=item.key,
                kind=item.kind,
                label=item.label,
                status="unconfirmed",
                memo="",
            )
            self._session.add(record)
        else:
            record.label = item.label
        if opportunity is not UNSET:
            record.opportunity = opportunity  # type: ignore[assignment]
        if status is not UNSET:
            record.status = status  # type: ignore[assignment]
            record.changed_by_change_request_number = None
        if memo is not UNSET:
            record.memo = memo  # type: ignore[assignment]
        record.updated_by = updated_by
        await self._session.flush()
        return record

    async def mark_confirmed(
        self, project_id: uuid.UUID, item: ConfirmationItem, *, updated_by: uuid.UUID | None
    ) -> ConfirmationItemRecord:
        """項目を確認済みにする(「採用にする」から。決める機会・メモは変えない)。"""
        return await self.save(project_id, item, status="confirmed", updated_by=updated_by)

    async def reopen(
        self, project_id: uuid.UUID, keys: Sequence[str], *, change_request_number: int
    ) -> list[str]:
        """確認済みの項目を未確認に戻し、戻した CR の番号を残す(CR の反映で文が変わった行)。
        確認済みでない行・行の無いキーは何もしない。決める機会・メモは変えない。戻したキーを
        返す。flushまで行う。"""
        if not keys:
            return []
        stmt = select(ConfirmationItemRecord).where(
            ConfirmationItemRecord.project_id == project_id,
            ConfirmationItemRecord.key.in_(keys),
            ConfirmationItemRecord.status == "confirmed",
        )
        records = list((await self._session.execute(stmt)).scalars().all())
        for record in records:
            record.status = "unconfirmed"
            record.changed_by_change_request_number = change_request_number
        if records:
            await self._session.flush()
        return [record.key for record in records]
