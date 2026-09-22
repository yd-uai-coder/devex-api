import uuid

from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation, Message
from app.repositories.base import CRUDRepository


class ConversationRepository(CRUDRepository[Conversation]):
    """Conversation/Messageモデルに対する永続化操作をまとめるリポジトリ。"""

    model = Conversation

    def _default_options(self) -> tuple:
        """会話取得時に関連するmessagesを常にEagerロードするオプションを返す。"""
        return (selectinload(Conversation.messages),)

    async def create(self, *, user_id: uuid.UUID, title: str | None = None) -> Conversation:
        """新規会話をセッションに追加し、flushしてIDを確定させた状態で返す。"""
        conversation = Conversation(user_id=user_id, title=title)
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def get_by_id(
        self, conversation_id: uuid.UUID, *, user_id: uuid.UUID
    ) -> Conversation | None:
        """会話IDと所有者IDの両方が一致する会話のみを取得する（他ユーザーの会話は取得できない）。"""
        return await self.find_one(id=conversation_id, user_id=user_id)

    async def list_for_user(self, user_id: uuid.UUID) -> list[Conversation]:
        """指定ユーザーの会話一覧を更新日時の降順で取得する。"""
        return await self.list_all(order_by=Conversation.updated_at.desc(), user_id=user_id)

    async def add_message(self, *, conversation_id: uuid.UUID, role: str, content: str) -> Message:
        """会話にメッセージを1件追加し、flushしてIDを確定させた状態で返す。"""
        message = Message(conversation_id=conversation_id, role=role, content=content)
        self._session.add(message)
        await self._session.flush()
        return message
