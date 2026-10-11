import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

DocType = Literal["requirements", "external_design", "internal_design", "implementation_plan"]


class DocumentIssueRead(BaseModel):
    """要件定義書・外部設計書の検証の指摘(app/documents/issues.py の DocumentIssue)。"""

    level: Literal["critical", "major", "minor"]
    code: str
    message: str
    targets: list[str]


class AssumedRequirementRead(BaseModel):
    """推測の要件(プレビューの「採用にする」の対象)。"""

    id: str
    kind: Literal["FR", "NFR", "CON"]
    text: str


class RequirementsSummaryRead(BaseModel):
    """要件定義書の出どころの件数(user / adopted / assumed)・推測の要件・検証の指摘。"""

    counts: dict[str, int]
    assumed: list[AssumedRequirementRead]
    issues: list[DocumentIssueRead]


class ExternalDesignSummaryRead(BaseModel):
    """外部設計書の行数(screens / apis / integrations)と検証の指摘。"""

    counts: dict[str, int]
    issues: list[DocumentIssueRead]


class GeneratedDocumentRead(BaseModel):
    """生成された設計書1件(最新バージョン)をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_type: DocType
    content: str
    version: int
    created_at: datetime
    is_current: bool
    # 要件定義書で model を持つ版だけ(ほかは None)。ルートが requirements_summary で入れる
    requirements: RequirementsSummaryRead | None = None
    # 外部設計書で model を持つ版だけ(ほかは None)。ルートが external_design_summary で入れる
    external_design: ExternalDesignSummaryRead | None = None
