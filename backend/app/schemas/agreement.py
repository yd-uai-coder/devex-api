import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.agreements import AgreedKind, AgreementOrigin, origin_label
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
