import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PromptTemplateRead(BaseModel):
    """選択可能なプロンプトテンプレート1件をAPIレスポンスとして返す際のスキーマ
    (docs/external_design.md 2.5節2項のデータ構造例に合わせ、system_promptも含める)。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    target_type: str
    system_prompt: str
    default_environment: dict | None
    created_at: datetime
