from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.detailed_design import StageState


class DesignStageRead(BaseModel):
    """段階1つ分の状態。未着手の段階も含めて、段階1〜7を常に返す(行が無ければversion等はNone)。

    `missing_inputs`は、まだそろっていない入力(`stage:<n>`=承認されていない前の段階、
    `doc:<doc_type>`=まだ無い文書)。空なら段階は開いていて、保存・承認できる。"""

    stage: int
    state: StageState
    is_open: bool
    missing_inputs: list[str]
    version: int | None
    approved_version: int | None
    model: dict[str, Any] | None
    updated_at: datetime | None


class DesignStageSave(BaseModel):
    """段階の保存リクエスト。`version`は画面が見ていた版(未着手の段階を初めて保存するときはNone)。"""

    version: int | None
    model: dict[str, Any]


class DesignStageApprove(BaseModel):
    """段階の承認リクエスト。`version`は画面が見ていた版(見ていない内容を承認しないため)。"""

    version: int
