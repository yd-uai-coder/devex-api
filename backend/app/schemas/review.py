import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.review import ReviewCause, ReviewLevel

# レビューの区切り(今は文書の生成の後だけ。段階6・7の承認の後は後で足す)
ReviewGate = Literal["documents"]


class ReviewFindingRead(BaseModel):
    """設計レビューの指摘1件(app/models/review.py の ReviewFinding)。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    level: ReviewLevel
    cause: ReviewCause
    targets: list[str]
    point_key: str | None
    problem: str
    evidence: str
    reason: str
    suggestion: str
    decision: str


class ReviewRunRead(BaseModel):
    """設計レビューの回。`is_current`はレビューした版が今表示中の文書の版と同じか(違えば、
    文書が変わった後で、まだレビューしていない)。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    gate: str
    targets: dict[str, int]
    trigger: str
    model: str
    status: str
    error_code: str | None
    input_tokens: int | None
    output_tokens: int | None
    started_at: datetime
    finished_at: datetime | None
    findings: list[ReviewFindingRead]
    is_current: bool = False
