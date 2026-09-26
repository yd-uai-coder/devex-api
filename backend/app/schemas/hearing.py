import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ChatSender = Literal["user", "ai", "intake", "others", "attachment"]


class HearingMessageRequest(BaseModel):
    """チャットヒアリングへのメッセージ送信APIのリクエストボディ。"""

    message: str


class ChatHistoryRead(BaseModel):
    """チャット履歴1件をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender: ChatSender
    message: str
    created_at: datetime
