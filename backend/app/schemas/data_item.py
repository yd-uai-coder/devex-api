import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.uml.domain import DataItemField


class DataItemRead(BaseModel):
    """データ辞書1件をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    fields: list[DataItemField]
    created_at: datetime
    updated_at: datetime


class DataItemCreate(BaseModel):
    """データ項目作成リクエストのスキーマ。"""

    name: str
    fields: list[DataItemField] = []


class DataItemUpdate(BaseModel):
    """データ項目更新リクエストのスキーマ(name/fieldsを丸ごと置き換える)。"""

    name: str
    fields: list[DataItemField] = []
