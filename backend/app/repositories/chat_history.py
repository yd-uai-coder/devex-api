import uuid

from app.models.chat_history import ChatHistory
from app.repositories.base import CRUDRepository


class ChatHistoryRepository(CRUDRepository[ChatHistory]):
    """ChatHistoryモデルに対する永続化操作をまとめるリポジトリ。"""

    model = ChatHistory

    async def add(
        self, *, project_id: uuid.UUID, sender: str, message: str, cards: list | None = None
    ) -> ChatHistory:
        """チャット履歴を1件追加し、flushしてIDを確定させた状態で返す(cardsはAIの発言の
        選択肢カード)。"""
        entry = ChatHistory(project_id=project_id, sender=sender, message=message, cards=cards)
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def list_for_project(self, project_id: uuid.UUID) -> list[ChatHistory]:
        """指定プロジェクトのチャット履歴を送信日時の昇順(発生順)で取得する。"""
        return await self.list_all(order_by=ChatHistory.created_at.asc(), project_id=project_id)
