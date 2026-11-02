import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.agreements import (
    ELEMENT_STATE_LABEL,
    RESTORE_REASON_LABEL,
    AgreedKind,
    AgreementOrigin,
    DocumentAgreementStatus,
    ElementHistory,
    ElementState,
    HistoryEvent,
    RestoreReason,
    origin_label,
)
from app.change_requests import TargetKind
from app.models.agreement import AgreementRecord

# 合意の記録の文の上限(相手は名前・会社名、メモは打ち合わせの要点)
COUNTERPART_MAX = 200
MEMO_MAX = 10000
REASON_MAX = 2000


class AgreementNotes(BaseModel):
    """合意の記録の共通の項目(合意済みの版の作成と、CR の合意・却下で同じ)。相手は空でもよい
    (空白だけは null にそろえる)。メモは空でもよく、理由は必須。日時は使う側で持つ
    (合意済みの版は`agreed_at`、CR は`decided_at`)。"""

    counterpart: str | None = Field(default=None, max_length=COUNTERPART_MAX)
    memo: str = Field(default="", max_length=MEMO_MAX)
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=REASON_MAX)
    ]

    @field_validator("counterpart")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class AgreementRead(BaseModel):
    """今の合意1件(要素1つの最新の記録)。`label`は記録した時点の表示名、`content`は合意した中身
    (行の model の写し)。`origin_label`は出どころの名前(B-1・採用にする・CR-1)。"""

    key: str
    kind: AgreedKind
    label: str
    content: dict[str, Any]
    origin: AgreementOrigin
    origin_label: str
    baseline_number: int | None
    change_request_number: int | None
    recorded_by: uuid.UUID | None
    created_at: datetime


def agreement_read(record: AgreementRecord) -> AgreementRead:
    """記録の行から応答を作る(出どころの束・CR は読み込み済みであること)。"""
    baseline_number = record.baseline.number if record.baseline is not None else None
    cr_number = record.change_request.number if record.change_request is not None else None
    origin: AgreementOrigin = record.origin  # type: ignore[assignment]
    return AgreementRead(
        key=record.key,
        kind=record.kind,  # type: ignore[arg-type]
        label=record.label,
        content=record.content or {},
        origin=origin,
        origin_label=origin_label(
            origin, baseline_number=baseline_number, cr_number=cr_number
        ),
        baseline_number=baseline_number,
        change_request_number=cr_number,
        recorded_by=record.recorded_by,
        created_at=record.created_at,
    )


class HistoryEntryRead(BaseModel):
    """要素の履歴の1行(app/agreements/history.py の HistoryEntry)。合意の記録の行は`origin`〜
    `recorded_by`を、戻しの行(`restored`)は`doc_type`〜`change_request_number`を持つ。"""

    event: HistoryEvent
    event_label: str
    occurred_at: datetime
    origin: AgreementOrigin | None
    origin_label: str | None
    label: str | None
    content: dict[str, Any] | None
    recorded_by: uuid.UUID | None
    doc_type: str | None
    doc_label: str | None
    version: int | None
    restore_reason: RestoreReason | None
    restore_reason_label: str | None
    change_request_number: int | None


class RelatedChangeRequestRead(BaseModel):
    """要素に関わる CR 1つ(対象の要件の変更の種類か、保存した予測に入っているか)。"""

    number: int
    title: str
    status: str
    status_label: str
    target_kind: TargetKind | None
    target_kind_label: str | None
    predicted: bool


class AgreementHistoryRead(BaseModel):
    """要素1つの履歴。`current`は今の合意の中身(合意していない・外れたなら null)。"""

    key: str
    kind: AgreedKind
    label: str
    current: dict[str, Any] | None
    entries: list[HistoryEntryRead]
    change_requests: list[RelatedChangeRequestRead]


def agreement_history_read(history: ElementHistory) -> AgreementHistoryRead:
    return AgreementHistoryRead(
        key=history.key,
        kind=history.kind,
        label=history.label,
        current=dict(history.current) if history.current is not None else None,
        entries=[
            HistoryEntryRead(
                event=e.event,
                event_label=e.event_label,
                occurred_at=e.occurred_at,
                origin=e.origin,  # type: ignore[arg-type]
                origin_label=e.origin_label,
                label=e.label,
                content=dict(e.content) if e.content is not None else None,
                recorded_by=e.recorded_by,
                doc_type=e.doc_type,
                doc_label=e.doc_label,
                version=e.version,
                restore_reason=e.restore_reason,
                restore_reason_label=e.restore_reason_label,
                change_request_number=e.change_request_number,
            )
            for e in history.entries
        ],
        change_requests=[
            RelatedChangeRequestRead(
                number=c.number,
                title=c.title,
                status=c.status,
                status_label=c.status_label,
                target_kind=c.target_kind,
                target_kind_label=c.target_kind_label,
                predicted=c.predicted,
            )
            for c in history.change_requests
        ],
    )


# 合意の状態を引ける文書(合意の対象の要素を持つ2文書)
AgreementDocType = Literal["requirements", "external_design"]


class ElementStatusRead(BaseModel):
    """表示中の版の要素1つの合意の状態(app/agreements/status.py の ElementStatus)。出どころの
    欄は、合意の記録がある要素(`unagreed`以外)だけが持つ。"""

    key: str
    kind: AgreedKind
    label: str
    state: ElementState
    state_label: str
    origin_label: str | None
    baseline_number: int | None
    change_request_number: int | None


class RestoredElementRead(BaseModel):
    """表示中の版で合意の形へ戻した要素1つ。"""

    key: str
    label: str
    reason: RestoreReason
    reason_label: str


class AgreementStatusRead(BaseModel):
    """文書1つの表示中の版の合意の状態。`version`は表示中の版(文書が無ければ null)、
    `change_request_number`は版が反映した CR(CR を渡さない再生成の版は null)。`restored`は、その
    版で合意の形へ戻した要素(CR の版なら、CR の予測に無いので戻したもの)。"""

    doc_type: AgreementDocType
    version: int | None
    change_request_number: int | None
    elements: list[ElementStatusRead]
    restored: list[RestoredElementRead]


def agreement_status_read(status: DocumentAgreementStatus) -> AgreementStatusRead:
    return AgreementStatusRead(
        doc_type=status.doc_type,  # type: ignore[arg-type]
        version=status.version,
        change_request_number=status.change_request_number,
        elements=[
            ElementStatusRead(
                key=e.key,
                kind=e.kind,
                label=e.label,
                state=e.state,
                state_label=ELEMENT_STATE_LABEL[e.state],
                origin_label=e.origin_label,
                baseline_number=e.baseline_number,
                change_request_number=e.change_request_number,
            )
            for e in status.elements
        ],
        restored=[
            RestoredElementRead(
                key=r.key,
                label=r.label,
                reason=r.reason,
                reason_label=RESTORE_REASON_LABEL[r.reason],
            )
            for r in status.restored
        ],
    )
