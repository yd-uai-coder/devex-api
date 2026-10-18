import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ProjectStatus = Literal["interviewing", "generating", "completed", "revising"]
IntakeFileStatus = Literal["processed", "failed"]
# simple: 簡易ドキュメントモード(4文書の一括生成)
# detailed: 詳細設計モード(docs/external_design.md 2.7節)
ProjectMode = Literal["simple", "detailed"]
# 一覧の1ページの件数(docs/external_design.md 2.12節)
PROJECT_PAGE_SIZE = 10


class IntakeFileRead(BaseModel):
    """添付ファイルの処理結果サマリをAPIレスポンスとして返す際のスキーマ(extracted_text本文は含めない)。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    file_type: str
    status: IntakeFileStatus
    error_message: str | None


class GenerationFailureRead(BaseModel):
    """直近の生成の失敗(理由のコード・利用者に見せる文言・作り直そうとした文書・日時)。"""

    reason_code: str
    message: str
    targets: list[str]
    failed_at: datetime


class ProjectRead(BaseModel):
    """プロジェクトの概要をAPIレスポンスとして返す際のスキーマ。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: ProjectStatus
    mode: ProjectMode
    # 直近の生成が失敗していれば、その記録(次の生成を受け付けると消える)
    generation_failure: GenerationFailureRead | None = None
    created_at: datetime
    updated_at: datetime


class ProjectListPage(BaseModel):
    """一覧の1ページ分と、条件に合う全件数(画面がページの数を出すため)。"""

    items: list[ProjectRead]
    total: int


class ProjectDetail(ProjectRead):
    """プロジェクトの詳細(初期ヒアリング入力・添付ファイルサマリを含む)をAPIレスポンスとして返す際のスキーマ。"""

    intake: dict | None
    intake_files: list[IntakeFileRead] = []
    template_id: uuid.UUID | None = None
