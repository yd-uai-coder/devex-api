import uuid
from datetime import UTC, datetime

from app.models.change_request_share_link import ChangeRequestShareLink
from app.repositories.base import CRUDRepository


class ChangeRequestShareLinkRepository(CRUDRepository[ChangeRequestShareLink]):
    """CR の共有リンクの永続化。リンクの状態を決めるのはサービス(と sharing.py)で、ここは行の
    作成・取り消し・読み出しを持つ。"""

    model = ChangeRequestShareLink

    async def list_for(self, change_request_id: uuid.UUID) -> list[ChangeRequestShareLink]:
        """CR のリンク(新しい順。取り消したものも)。"""
        return await self.list_all(
            order_by=ChangeRequestShareLink.created_at.desc(), change_request_id=change_request_id
        )

    async def active_for(self, change_request_id: uuid.UUID) -> ChangeRequestShareLink | None:
        """CR の取り消していないリンク(期限切れも含む。無ければ None)。"""
        return await self.find_one(change_request_id=change_request_id, revoked_at=None)

    async def get_by_hash(self, token_hash: str) -> ChangeRequestShareLink | None:
        """トークンの sha256 でリンクを引く。無ければ None。"""
        return await self.find_one(token_hash=token_hash)

    async def create(
        self,
        *,
        change_request_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        created_by: uuid.UUID | None,
    ) -> ChangeRequestShareLink:
        """リンクを作る。flushまで行う。"""
        link = ChangeRequestShareLink(
            change_request_id=change_request_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by=created_by,
            created_at=datetime.now(UTC),
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def revoke(self, link: ChangeRequestShareLink, *, revoked_by: uuid.UUID | None) -> None:
        """リンクを取り消す(行は残す)。flushまで行う。"""
        link.revoked_at = datetime.now(UTC)
        link.revoked_by = revoked_by
        await self._session.flush()
