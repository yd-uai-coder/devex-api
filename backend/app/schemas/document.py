import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

DocType = Literal["requirements", "external_design", "internal_design", "implementation_plan"]


class GeneratedDocumentRead(BaseModel):
    """生成された設計書1件(最新バージョン)をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_type: DocType
    content: str
    version: int
    created_at: datetime
    is_current: bool
