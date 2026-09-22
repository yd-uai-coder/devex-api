import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

MessageRole = Literal["user", "assistant", "system", "tool"]


class MessageRead(BaseModel):
    """メッセージ1件をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime


class ConversationRead(BaseModel):
    """会話の概要（メッセージ本文を含まない）をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationRead):
    """会話の詳細（メッセージ一覧を含む）をAPIレスポンスとして返す際のスキーマ。"""

    messages: list[MessageRead] = []


class ChatRequest(BaseModel):
    """チャットメッセージ送信APIのリクエストボディ。"""

    conversation_id: uuid.UUID | None = None
    message: str


class ChatResponse(BaseModel):
    """チャットメッセージ送信APIのレスポンスボディ。"""

    conversation_id: uuid.UUID
    message: MessageRead
