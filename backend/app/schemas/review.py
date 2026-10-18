import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.review import (
    FixVerdict,
    FollowupState,
    GateStatus,
    ReviewCause,
    ReviewGate,
    ReviewLevel,
)

# 指摘の採否(人が決める)。pending = 未判断
FindingDecision = Literal["pending", "adopted", "rejected", "held"]

__all__ = [
    "FindingDecision",
    "FindingDecisionUpdate",
    "FixCheckRead",
    "FollowupState",
    "RehearingRequest",
    "ReviewFindingRead",
    "ReviewGate",
    "ReviewGateSummaryRead",
    "ReviewRunRead",
]


class FixCheckRead(BaseModel):
    """採用した指摘が直ったかの判定(app/models/review.py の ReviewFixCheck。シャドーモード)。
    今の版について判定したものだけを返す。"""

    model_config = ConfigDict(from_attributes=True)

    verdict: FixVerdict | None
    reason: str
    model: str
    error_code: str | None
    created_at: datetime


class ReviewFindingRead(BaseModel):
    """設計レビューの指摘1件(app/models/review.py の ReviewFinding)。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    level: ReviewLevel
    cause: ReviewCause
    targets: list[str]
    # 直すときに書き換える文書の種類(上流から)。列を足す前の指摘は null
    documents: list[str] | None
    point_key: str | None
    problem: str
    evidence: str
    reason: str
    suggestion: str
    decision: FindingDecision
    # 採用した指摘の追跡(unaddressed = 関連文書・段階の版が変わっていない / target_unchanged =
    # 版は変わったが対象の行が変わっていない / changed = 対象が変わった・比べられない)。
    # 採用していない指摘は null
    followup: FollowupState | None = None
    # 今の版についての「直ったか」の判定(判定していなければ null)
    fix_check: FixCheckRead | None = None


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


class ReviewGateSummaryRead(BaseModel):
    """区切り1つ分の状態(レビューのパネルの区切りの表)。`ready`は区切りの文書がそろっているか、
    `status`は not_ready / unreviewed / running / reviewed / failed、`run`は最新の回
    (無ければ null)、`adopted_before`は前の回で採用した指摘。"""

    gate: ReviewGate
    ready: bool
    status: GateStatus
    run: ReviewRunRead | None
    # 前の回で採用した指摘(新しい回から。追跡の状態付き)
    adopted_before: list[ReviewFindingRead] = []


class RehearingRequest(BaseModel):
    """聞き直しの要求。`finding_ids`は採用したヒアリングの指摘。"""

    finding_ids: list[uuid.UUID]


class FindingDecisionUpdate(BaseModel):
    """指摘の採否の変更。"""

    decision: FindingDecision
