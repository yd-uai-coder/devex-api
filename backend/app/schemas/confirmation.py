from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.confirmations import (
    OPPORTUNITY_LABEL,
    STATUS_LABEL,
    ConfirmationItem,
    ConfirmationKind,
    ConfirmationStatus,
    Opportunity,
    adoption_counts,
)
from app.models.confirmation import ConfirmationItemRecord

# メモの上限(打ち合わせで聞くことの要点)
CONFIRMATION_MEMO_MAX = 2000


class ConfirmationItemRead(BaseModel):
    """確認事項1件。`present`は今の一覧にあるか(False は、利用者が状態を決めた後に一覧から消えた
    項目。採用にして推測でなくなった行など)。`refs`は観点に頼っている要件の ID(1.2 は`1.2`)。
    決める機会・状態・メモは、利用者が決めていなければ 未設定・未確認・空。"""

    key: str
    kind: ConfirmationKind
    label: str
    refs: list[str]
    present: bool
    status: ConfirmationStatus
    status_label: str
    opportunity: Opportunity | None
    opportunity_label: str | None
    memo: str
    updated_at: datetime | None


class AdoptionRead(BaseModel):
    """推測の採用率(推測の要件・推測の利用者)。`rate`は 確認済み ÷(確認済み + 却下)で、分母が
    0なら None。未確認は今の一覧にあるものだけ数える。"""

    confirmed: int
    rejected: int
    unconfirmed: int
    rate: float | None


class ConfirmationListRead(BaseModel):
    """確認事項の一覧と、推測の採用率。"""

    items: list[ConfirmationItemRead]
    adoption: AdoptionRead


class ConfirmationUpdate(BaseModel):
    """確認事項1件の、利用者が決めた欄の部分更新(渡した欄だけ変える)。決める機会は null で
    未設定に戻せる。状態とメモは null にできない。"""

    opportunity: Opportunity | None = None
    status: ConfirmationStatus | None = None
    memo: str | None = Field(default=None, max_length=CONFIRMATION_MEMO_MAX)

    @field_validator("status", "memo")
    @classmethod
    def _not_null(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("null にはできません")
        return value


def confirmation_list_read(
    items: Sequence[ConfirmationItem], records: Sequence[ConfirmationItemRecord]
) -> ConfirmationListRead:
    """今の一覧の項目に、利用者が決めた欄を重ねる。今の一覧に無くても状態を決めた行(確認済み・
    却下)は、保存した表示名で後ろに並べる(作った順)。採用率もここで数える。"""
    by_key = {record.key: record for record in records}
    present_keys = {item.key for item in items}
    rows = [_item_read(item, by_key.get(item.key), present=True) for item in items]
    rows += [
        _item_read(
            ConfirmationItem(record.key, record.kind, record.label),  # type: ignore[arg-type]
            record,
            present=False,
        )
        for record in records
        if record.key not in present_keys and record.status != "unconfirmed"
    ]
    counts = adoption_counts((row.kind, row.status, row.present) for row in rows)
    return ConfirmationListRead(
        items=rows,
        adoption=AdoptionRead(
            confirmed=counts.confirmed,
            rejected=counts.rejected,
            unconfirmed=counts.unconfirmed,
            rate=counts.rate,
        ),
    )


def _item_read(
    item: ConfirmationItem, record: ConfirmationItemRecord | None, *, present: bool
) -> ConfirmationItemRead:
    status: ConfirmationStatus = record.status if record else "unconfirmed"  # type: ignore[assignment]
    opportunity: Opportunity | None = record.opportunity if record else None  # type: ignore[assignment]
    return ConfirmationItemRead(
        key=item.key,
        kind=item.kind,
        label=item.label,
        refs=list(item.refs),
        present=present,
        status=status,
        status_label=STATUS_LABEL[status],
        opportunity=opportunity,
        opportunity_label=OPPORTUNITY_LABEL[opportunity] if opportunity else None,
        memo=record.memo if record else "",
        updated_at=record.updated_at if record else None,
    )
