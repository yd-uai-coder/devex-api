import uuid
from datetime import UTC, datetime

from app.models.change_request_summary import ChangeRequestSummary
from app.repositories.base import CRUDRepository


class ChangeRequestSummaryRepository(CRUDRepository[ChangeRequestSummary]):
    """説明書の要約(CR ごとに1行)の永続化。状態を決めるのはサービスで、ここは行の作成・上書きと
    読み出しを持つ。"""

    model = ChangeRequestSummary

    async def get_for(self, change_request_id: uuid.UUID) -> ChangeRequestSummary | None:
        """CR の要約。無ければ None。"""
        return await self.find_one(change_request_id=change_request_id)

    async def replace(
        self,
        *,
        change_request_id: uuid.UUID,
        text: str,
        fingerprint: str,
        status: str,
        generated_model: str | None,
        generated_by: uuid.UUID | None,
    ) -> ChangeRequestSummary:
        """要約を作る(あれば上書きし、直した文・確かめた記録を消す)。元の文と今の文は同じ文で
        始める。flushまで行う。"""
        summary = await self.get_for(change_request_id)
        if summary is None:
            summary = ChangeRequestSummary(change_request_id=change_request_id)
            self._session.add(summary)
        summary.generated_text = text
        summary.text = text
        summary.status = status
        summary.fingerprint = fingerprint
        summary.generated_model = generated_model
        summary.generated_by = generated_by
        summary.generated_at = datetime.now(UTC)
        summary.edited_at = None
        summary.confirmed_by = None
        summary.confirmed_at = None
        await self._session.flush()
        return summary
