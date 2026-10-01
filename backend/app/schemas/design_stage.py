from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.detailed_design import StageState


class StageIssueRead(BaseModel):
    """段階ごとの検証の指摘1件(app/detailed_design/validation.py の StageIssue)。
    `error`があると承認できない。`warning`は承認を止めない。"""

    severity: Literal["error", "warning"]
    code: str
    message: str
    target: str | None = None


class DesignStageRead(BaseModel):
    """段階1つ分の状態。未着手の段階も含めて、段階1〜7を常に返す(行が無ければversion等はNone)。

    `missing_inputs`は、まだそろっていない入力(`stage:<n>`=承認されていない前の段階、
    `doc:<doc_type>`=まだ無い文書)。空なら段階は開いていて、保存・承認できる。

    `generation_status`はAIの下書きの生成の状態(None=まだ生成していない/generating/completed/
    failed)、`generation_error`は直近の生成が失敗した理由(ユーザー向けの文言)。`issues`は段階ごとの
    検証の結果(Phase 16)。"""

    stage: int
    state: StageState
    is_open: bool
    missing_inputs: list[str]
    version: int | None
    approved_version: int | None
    model: dict[str, Any] | None
    updated_at: datetime | None
    generation_status: Literal["generating", "completed", "failed"] | None = None
    generation_error: str | None = None
    issues: list[StageIssueRead] = Field(default_factory=list)


class DesignStageSave(BaseModel):
    """段階の保存リクエスト。`version`は画面が見ていた版(未着手の段階を初めて保存するときはNone)。"""

    version: int | None
    model: dict[str, Any]


class DesignStageApprove(BaseModel):
    """段階の承認リクエスト。`version`は画面が見ていた版(見ていない内容を承認しないため)。"""

    version: int
