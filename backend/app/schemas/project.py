import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ProjectStatus = Literal["interviewing", "generating", "completed", "revising"]
IntakeFileStatus = Literal["processed", "failed"]


class IntakeFileRead(BaseModel):
    """添付ファイルの処理結果サマリをAPIレスポンスとして返す際のスキーマ(extracted_text本文は含めない)。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    file_type: str
    status: IntakeFileStatus
    error_message: str | None


class ProjectRead(BaseModel):
    """プロジェクトの概要をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectRead):
    """プロジェクトの詳細(初期ヒアリング入力・添付ファイルサマリを含む)をAPIレスポンスとして返す際のスキーマ。"""

    intake: dict | None
    intake_files: list[IntakeFileRead] = []
    template_id: uuid.UUID | None = None
