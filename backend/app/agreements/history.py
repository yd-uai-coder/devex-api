"""要素1つの合意の履歴(純粋関数。docs/external_design.md 2.15節)。

要素のキー(`req:FR-001`・`users`・`screen:SCR-001`・`api:GET /x`・`integration:<サービス名>`)
ごとに、「いつ・何で・どう変わったか」を時系列に並べる。材料は3つ。

- 合意の記録(追記型で消えない): 最初の合意・中身の変わった合意・同じ中身での合意し直し・外れた。
  中身は`body_of`で比べる(束を作り直すたびに、変わっていない要素も記録が足されるため)。
- 版の`protection`(再生成の後に合意の形へ戻した要素): 戻した版を挟む。版は巡回で消えうるので、
  残っている版の分だけ出る(合意の中身の変化は記録で追える。戻しは補助の情報)。
- CR: 対象の要件に要素を持つ CR と、保存した予測の文書の側(画面・API)に要素が入った CR。
  状態は問わない(却下・取り下げも、要素に関わった記録として出す)。
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, get_args

from app.agreements.items import AgreedKind, body_of, kind_of
from app.agreements.protection import RESTORE_REASONS, RestoreReason
from app.change_requests import (
    STATUS_LABEL,
    TARGET_KIND_LABEL,
    Target,
    TargetKind,
)
from app.change_requests.explanation import DOCUMENT_LABEL

HistoryEvent = Literal["agreed", "changed", "reaffirmed", "removed", "restored"]

HISTORY_EVENTS: tuple[HistoryEvent, ...] = get_args(HistoryEvent)
HISTORY_EVENT_LABEL: dict[HistoryEvent, str] = {
    "agreed": "合意",
    "changed": "合意の中身が変わった",
    "reaffirmed": "同じ中身で合意し直した",
    "removed": "合意から外れた",
    "restored": "再生成の後に合意の形へ戻した",
}
RESTORE_REASON_LABEL: dict[RestoreReason, str] = {
    "changed": "書き換えを戻した",
    "dropped": "落ちた要素を戻した",
}


@dataclass(frozen=True)
class RecordFacts:
    """合意の記録の1行(DB の行から写したもの)。`content`が None は「外れた」。"""

    seq: int
    created_at: datetime
    label: str
    content: Mapping[str, Any] | None
    origin: str
    origin_label: str
    recorded_by: Any = None


@dataclass(frozen=True)
class VersionFacts:
    """合意を守った版1つ。`protection`は版の列の値(`[{key, reason}]`)。"""

    doc_type: str
    version: int
    created_at: datetime
    protection: Sequence[Mapping[str, Any]]
    change_request_number: int | None = None


@dataclass(frozen=True)
class ChangeRequestRef:
    """CR 1つの、要素との関わりを決める値。`predicted_keys`は保存した予測の文書の側の要素のキー。"""

    number: int
    title: str
    status: str
    targets: Sequence[Target]
    predicted_keys: Collection[str]


@dataclass(frozen=True)
class HistoryEntry:
    """履歴の1行。合意の記録の行は出どころ・中身を、戻しの行は文書の版・戻した理由を持つ。"""

    event: HistoryEvent
    event_label: str
    occurred_at: datetime
    origin: str | None = None
    origin_label: str | None = None
    label: str | None = None
    content: Mapping[str, Any] | None = None
    recorded_by: Any = None
    doc_type: str | None = None
    doc_label: str | None = None
    version: int | None = None
    restore_reason: RestoreReason | None = None
    restore_reason_label: str | None = None
    change_request_number: int | None = None


@dataclass(frozen=True)
class RelatedChangeRequest:
    """要素に関わる CR 1つ。`target_kind`は要素が対象の要件のときの変更の種類、`predicted`は
    保存した予測に要素が入っているか。"""

    number: int
    title: str
    status: str
    status_label: str
    target_kind: TargetKind | None
    target_kind_label: str | None
    predicted: bool


@dataclass(frozen=True)
class ElementHistory:
    """要素1つの履歴。`label`は最新の記録の表示名(記録が無ければキー)、`current`は今の合意の
    中身(合意していない・外れたなら None)。"""

    key: str
    kind: AgreedKind
    label: str
    current: Mapping[str, Any] | None
    entries: tuple[HistoryEntry, ...]
    change_requests: tuple[RelatedChangeRequest, ...]


def element_history(
    key: str,
    records: Sequence[RecordFacts],
    versions: Sequence[VersionFacts],
    change_requests: Sequence[ChangeRequestRef],
) -> ElementHistory:
    """要素1つの履歴。`records`はプロジェクトの合意の記録のうちこのキーのもの(どの順でもよい)、
    `versions`は合意を守った版、`change_requests`はプロジェクトの CR。合意の対象でないキーは
    ValueError。時系列は日時の順(同じ日時は、戻しの行 → 記録の順)。"""
    kind = kind_of(key)
    if kind is None:
        raise ValueError(f"合意の対象でない要素のキーです: {key}")
    ordered = sorted(records, key=lambda r: r.seq)
    timeline = [((r.created_at, 1, r.seq), entry) for r, entry in _record_entries(kind, ordered)]
    timeline += [((v.created_at, 0, v.version), entry) for v, entry in _restored(key, versions)]
    latest = ordered[-1] if ordered else None
    return ElementHistory(
        key=key,
        kind=kind,
        label=latest.label if latest is not None else key,
        current=latest.content if latest is not None else None,
        entries=tuple(entry for _, entry in sorted(timeline, key=lambda t: t[0])),
        change_requests=tuple(_related(key, change_requests)),
    )


def _record_entries(
    kind: AgreedKind, records: Sequence[RecordFacts]
) -> list[tuple[RecordFacts, HistoryEntry]]:
    entries: list[tuple[RecordFacts, HistoryEntry]] = []
    previous: str | None = None
    for record in records:
        body = body_of(kind, dict(record.content)) if record.content is not None else None
        if body is None:
            event: HistoryEvent = "removed"
        elif previous is None:
            event = "agreed"
        else:
            event = "reaffirmed" if body == previous else "changed"
        previous = body
        entries.append(
            (
                record,
                HistoryEntry(
                    event=event,
                    event_label=HISTORY_EVENT_LABEL[event],
                    occurred_at=record.created_at,
                    origin=record.origin,
                    origin_label=record.origin_label,
                    label=record.label,
                    content=record.content,
                    recorded_by=record.recorded_by,
                ),
            )
        )
    return entries


def _restored(
    key: str, versions: Sequence[VersionFacts]
) -> list[tuple[VersionFacts, HistoryEntry]]:
    """版の`protection`にキーがある版の、戻しの行。形の正しくない値は読まない。"""
    entries: list[tuple[VersionFacts, HistoryEntry]] = []
    for version in versions:
        reason = next(
            (
                item.get("reason")
                for item in version.protection
                if isinstance(item, Mapping) and item.get("key") == key
            ),
            None,
        )
        if reason not in RESTORE_REASONS:
            continue
        restore_reason: RestoreReason = reason  # type: ignore[assignment]
        entries.append(
            (
                version,
                HistoryEntry(
                    event="restored",
                    event_label=HISTORY_EVENT_LABEL["restored"],
                    occurred_at=version.created_at,
                    doc_type=version.doc_type,
                    doc_label=DOCUMENT_LABEL.get(version.doc_type, version.doc_type),
                    version=version.version,
                    restore_reason=restore_reason,
                    restore_reason_label=RESTORE_REASON_LABEL[restore_reason],
                    change_request_number=version.change_request_number,
                ),
            )
        )
    return entries


def _related(
    key: str, change_requests: Sequence[ChangeRequestRef]
) -> list[RelatedChangeRequest]:
    """要素に関わる CR(番号の順)。"""
    related: list[RelatedChangeRequest] = []
    for cr in sorted(change_requests, key=lambda c: c.number):
        target_kind: TargetKind | None = next(
            (t.kind for t in cr.targets if f"req:{t.requirement_id}" == key), None
        )
        predicted = key in cr.predicted_keys
        if target_kind is None and not predicted:
            continue
        related.append(
            RelatedChangeRequest(
                number=cr.number,
                title=cr.title,
                status=cr.status,
                status_label=STATUS_LABEL.get(cr.status, cr.status),  # type: ignore[call-overload]
                target_kind=target_kind,
                target_kind_label=TARGET_KIND_LABEL[target_kind] if target_kind else None,
                predicted=predicted,
            )
        )
    return related
