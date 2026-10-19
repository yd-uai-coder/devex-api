import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.change_requests import (
    BACKGROUND_SOURCE_LABEL,
    STATUS_LABEL,
    BackgroundSource,
    ChangeRequestAction,
    ChangeRequestStatus,
    Overlap,
)
from app.schemas.agreement import MEMO_MAX, REASON_MAX, AgreementNotes

if TYPE_CHECKING:
    from app.models.change_request import ChangeRequest

# CR の文の上限(タイトルは一覧の1行、内容は変更の申し出の本文)
TITLE_MAX = 200
CONTENT_MAX = 20000
# 1つの CR の対象の要件の数の上限
TARGETS_MAX = 100

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX)]
Content = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CONTENT_MAX)
]
RequirementIds = Annotated[list[str], Field(max_length=TARGETS_MAX)]


class ChangeRequestCreate(BaseModel):
    """CR を作るときの本文。背景は出どころの種類・日付(日の粒度。任意)・メモ。対象は基準の束の
    要件定義書にある要件 ID と、新しい要件を足すかの印(どちらか一方は要る。サービスで確かめる)。
    基準の束の番号を省くと、最新の束を基準にする。"""

    title: Title
    content: Content
    background_source: BackgroundSource
    background_date: date | None = None
    background_memo: str = Field(default="", max_length=MEMO_MAX)
    target_requirement_ids: RequirementIds = Field(default_factory=list)
    adds_requirement: bool = False
    baseline_number: int | None = Field(default=None, ge=1)


class ChangeRequestUpdate(BaseModel):
    """CR の中身の更新(送った項目だけを変える)。基準の束は変えない。`background_date`は null で
    消せる。"""

    title: Title | None = None
    content: Content | None = None
    background_source: BackgroundSource | None = None
    background_date: date | None = None
    background_memo: str | None = Field(default=None, max_length=MEMO_MAX)
    target_requirement_ids: RequirementIds | None = None
    adds_requirement: bool | None = None


class ChangeRequestDecision(AgreementNotes):
    """CR の合意・却下の記録。相手・メモ・理由は合意済みの版と同じ(AgreementNotes)。判断の日時は
    記録する日時と別に、タイムゾーン付きで受け取る。"""

    decision: Literal["agreed", "rejected"]
    decided_at: AwareDatetime


class ChangeRequestWithdraw(BaseModel):
    """CR の取り下げ。理由は任意(時系列に残す)。"""

    reason: str = Field(default="", max_length=REASON_MAX)


class ChangeRequestEventRead(BaseModel):
    """CR の時系列の1行。`from_status`は作成・写しで null。"""

    model_config = ConfigDict(from_attributes=True)

    action: str
    from_status: str | None
    to_status: str
    occurred_at: datetime
    actor_id: uuid.UUID | None
    note: str


class ChangeRequestDecisionRead(BaseModel):
    """CR の合意・却下の記録(合意済みの版と同じ項目)。"""

    counterpart: str | None
    decided_at: datetime
    memo: str
    reason: str
    recorded_by: uuid.UUID | None


class ChangeRequestOverlapRead(BaseModel):
    """同じ要件を対象にする、ほかの進行中の CR。"""

    number: int
    label: str
    requirement_ids: list[str]


class ChangeRequestSummaryRead(BaseModel):
    """CR 1つ(一覧用)。`label`は画面に出す名前(CR-1…)、`baseline_label`は基準の束(B-1…)。"""

    number: int
    label: str
    title: str
    status: ChangeRequestStatus
    status_label: str
    background_source: BackgroundSource
    background_source_label: str
    background_date: date | None
    target_requirement_ids: list[str]
    adds_requirement: bool
    baseline_number: int
    baseline_label: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, change_request: "ChangeRequest") -> "ChangeRequestSummaryRead":
        return cls.model_validate(summary_fields(change_request))


class ChangeRequestRead(ChangeRequestSummaryRead):
    """CR 1つ(詳細)。中身・合意の記録・時系列・重なり・写しの元と先・今できる操作。"""

    content: str
    background_memo: str
    decision: ChangeRequestDecisionRead | None
    events: list[ChangeRequestEventRead]
    overlapping: list[ChangeRequestOverlapRead]
    derived_from_number: int | None
    derived_numbers: list[int]
    allowed_actions: list[ChangeRequestAction]

    @classmethod
    def build(
        cls,
        change_request: "ChangeRequest",
        *,
        overlapping: list[Overlap],
        derived_from_number: int | None,
        derived_numbers: list[int],
        allowed_actions: list[ChangeRequestAction],
    ) -> "ChangeRequestRead":
        decision = None
        if change_request.decided_at is not None:
            decision = ChangeRequestDecisionRead(
                counterpart=change_request.decision_counterpart,
                decided_at=change_request.decided_at,
                memo=change_request.decision_memo or "",
                reason=change_request.decision_reason or "",
                recorded_by=change_request.decision_recorded_by,
            )
        return cls.model_validate(
            {
                **summary_fields(change_request),
                "content": change_request.content,
                "background_memo": change_request.background_memo,
                "decision": decision,
                "events": [
                    ChangeRequestEventRead.model_validate(event)
                    for event in change_request.events
                ],
                "overlapping": [
                    ChangeRequestOverlapRead(
                        number=o.number,
                        label=change_request_label(o.number),
                        requirement_ids=o.requirement_ids,
                    )
                    for o in overlapping
                ],
                "derived_from_number": derived_from_number,
                "derived_numbers": derived_numbers,
                "allowed_actions": allowed_actions,
            }
        )


def change_request_label(number: int) -> str:
    return f"CR-{number}"


def summary_fields(change_request: "ChangeRequest") -> dict[str, Any]:
    """一覧と詳細に共通の項目(基準の束は読み込み済みであること)。"""
    status: ChangeRequestStatus = change_request.status  # type: ignore[assignment]
    source: BackgroundSource = change_request.background_source  # type: ignore[assignment]
    baseline_number = change_request.baseline.number
    return {
        "number": change_request.number,
        "label": change_request_label(change_request.number),
        "title": change_request.title,
        "status": status,
        "status_label": STATUS_LABEL[status],
        "background_source": source,
        "background_source_label": BACKGROUND_SOURCE_LABEL[source],
        "background_date": change_request.background_date,
        "target_requirement_ids": list(change_request.target_requirement_ids),
        "adds_requirement": change_request.adds_requirement,
        "baseline_number": baseline_number,
        "baseline_label": f"B-{baseline_number}",
        "created_by": change_request.created_by,
        "created_at": change_request.created_at,
        "updated_at": change_request.updated_at,
    }
