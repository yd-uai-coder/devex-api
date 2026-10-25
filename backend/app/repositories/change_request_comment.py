import uuid
from datetime import UTC, datetime

from app.models.change_request_comment import ChangeRequestComment
from app.repositories.base import CRUDRepository


class ChangeRequestCommentRepository(CRUDRepository[ChangeRequestComment]):
    """CR のコメント欄の永続化。並べ方と返信できるかを決めるのはサービス(と comments.py)で、
    ここは行の作成・読み出しを持つ。"""

    model = ChangeRequestComment

    async def list_for(self, change_request_id: uuid.UUID) -> list[ChangeRequestComment]:
        """CR のコメントと返信(古い順)。"""
        return await self.list_all(
            order_by=ChangeRequestComment.created_at, change_request_id=change_request_id
        )

    async def create(
        self,
        *,
        change_request_id: uuid.UUID,
        author_kind: str,
        body: str,
        fingerprint: str,
        author_name: str = "",
        author_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
        share_link_id: uuid.UUID | None = None,
    ) -> ChangeRequestComment:
        """コメント(返信)を作る。flushまで行う。"""
        comment = ChangeRequestComment(
            change_request_id=change_request_id,
            parent_id=parent_id,
            share_link_id=share_link_id,
            author_kind=author_kind,
            author_name=author_name,
            author_id=author_id,
            body=body,
            fingerprint=fingerprint,
            created_at=datetime.now(UTC),
        )
        self._session.add(comment)
        await self._session.flush()
        return comment
