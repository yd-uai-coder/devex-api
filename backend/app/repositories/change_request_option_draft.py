import uuid
from datetime import UTC, datetime
from typing import Any

from app.models.change_request_option_draft import ChangeRequestOptionDraft
from app.repositories.base import CRUDRepository


class ChangeRequestOptionDraftRepository(CRUDRepository[ChangeRequestOptionDraft]):
    """選択肢の下書き(CR ごとに1行)の永続化。状態を決めるのはサービスで、ここは行の作成・上書きと
    読み出しを持つ。"""

    model = ChangeRequestOptionDraft

    async def get_for(self, change_request_id: uuid.UUID) -> ChangeRequestOptionDraft | None:
        """CR の下書き。無ければ None。"""
        return await self.find_one(change_request_id=change_request_id)

    async def replace(
        self,
        *,
        change_request_id: uuid.UUID,
        options: dict[str, Any],
        fingerprint: str,
        status: str,
        generated_model: str | None,
        generated_by: uuid.UUID | None,
    ) -> ChangeRequestOptionDraft:
        """下書きを作る(あれば上書きし、取り込んだ記録を消す)。flushまで行う。"""
        draft = await self.get_for(change_request_id)
        if draft is None:
            draft = ChangeRequestOptionDraft(change_request_id=change_request_id)
            self._session.add(draft)
        draft.options = options
        draft.status = status
        draft.fingerprint = fingerprint
        draft.generated_model = generated_model
        draft.generated_by = generated_by
        draft.generated_at = datetime.now(UTC)
        draft.adopted_by = None
        draft.adopted_at = None
        await self._session.flush()
        return draft
