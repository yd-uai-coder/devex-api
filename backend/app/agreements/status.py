"""表示中の版の要素ごとの合意の状態(純粋関数。docs/external_design.md 2.15節)。

文書のプレビュー(SCR-005)の「合意の状態」に出す。表示中の版の要素(`agreement_items`)と今の
合意(要素ごとの最新の記録)を、要素のキーで突き合わせる。

- `agreed`: 合意と同じ中身(`body_of`で比べる)。
- `differs`: 合意と違う中身。CR を反映した後で合意の更新の前の版や、復元した古い版を表示している
  ときに出る(保護は再生成の後に戻すので、ふつうの再生成の版では出ない)。
- `unagreed`: 合意の記録が無い。CR で足した要素や、合意済みの版(束)の後に増えた要素。
- `missing`: 今の合意にあり、表示中の版に無い(古い版の表示など)。末尾に今の合意の順で並べる。

あわせて、版の`protection`(再生成の後に合意の形へ戻した要素)に表示名を付ける。
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from app.agreements.items import AgreedItem, AgreedKind, body_of
from app.agreements.protection import RESTORE_REASONS, RestoreReason

ElementState = Literal["agreed", "differs", "unagreed", "missing"]

ELEMENT_STATES: tuple[ElementState, ...] = get_args(ElementState)
ELEMENT_STATE_LABEL: dict[ElementState, str] = {
    "agreed": "合意済み",
    "differs": "合意と違う",
    "unagreed": "未合意",
    "missing": "今の版に無い",
}


@dataclass(frozen=True)
class CurrentAgreement:
    """今の合意1件(要素1つの最新の記録)。`content`は合意した中身(外れた記録は渡さない)。"""

    key: str
    kind: AgreedKind
    label: str
    content: Mapping[str, Any]
    origin_label: str
    baseline_number: int | None = None
    change_request_number: int | None = None


@dataclass(frozen=True)
class ElementStatus:
    """要素1つの合意の状態。合意の記録がある要素(`unagreed`以外)は出どころを持つ。"""

    key: str
    kind: AgreedKind
    label: str
    state: ElementState
    state_label: str
    origin_label: str | None = None
    baseline_number: int | None = None
    change_request_number: int | None = None


def element_states(
    items: Sequence[AgreedItem],
    agreements: Sequence[CurrentAgreement],
    kinds: Collection[AgreedKind],
) -> list[ElementStatus]:
    """表示中の版の要素(`items`。文書の並び順)ごとの合意の状態に、表示中の版に無い合意済みの
    要素(`kinds`の種類だけ。今の合意の順)を続ける。"""
    by_key = {a.key: a for a in agreements}
    present = {item.key for item in items}
    result = [_status(item, by_key.get(item.key)) for item in items]
    result += [
        _with_origin(a.key, a.kind, a.label, "missing", a)
        for a in agreements
        if a.kind in kinds and a.key not in present
    ]
    return result


def _status(item: AgreedItem, agreement: CurrentAgreement | None) -> ElementStatus:
    if agreement is None:
        return ElementStatus(
            item.key, item.kind, item.label, "unagreed", ELEMENT_STATE_LABEL["unagreed"]
        )
    same = body_of(item.kind, item.content) == body_of(item.kind, dict(agreement.content))
    return _with_origin(item.key, item.kind, item.label, "agreed" if same else "differs", agreement)


def _with_origin(
    key: str, kind: AgreedKind, label: str, state: ElementState, agreement: CurrentAgreement
) -> ElementStatus:
    return ElementStatus(
        key,
        kind,
        label,
        state,
        ELEMENT_STATE_LABEL[state],
        origin_label=agreement.origin_label,
        baseline_number=agreement.baseline_number,
        change_request_number=agreement.change_request_number,
    )


@dataclass(frozen=True)
class RestoredElement:
    """版で合意の形へ戻した要素1つ(表示名つき)。"""

    key: str
    label: str
    reason: RestoreReason


def restored_elements(
    protection: Sequence[Mapping[str, Any]],
    items: Sequence[AgreedItem],
    agreements: Sequence[CurrentAgreement],
) -> list[RestoredElement]:
    """版の`protection`(`[{key, reason}]`)に表示名を付ける。表示名は表示中の版の要素、無ければ
    今の合意の記録、どちらにも無ければキーそのもの。知らない理由の行は飛ばす(列は JSON のため)。"""
    labels = {a.key: a.label for a in agreements} | {item.key: item.label for item in items}
    result: list[RestoredElement] = []
    for entry in protection:
        key, reason = entry.get("key"), entry.get("reason")
        if isinstance(key, str) and reason in RESTORE_REASONS:
            result.append(RestoredElement(key, labels.get(key, key), reason))
    return result


@dataclass(frozen=True)
class DocumentAgreementStatus:
    """文書1つの表示中の版の合意の状態。`version`は表示中の版(文書が無ければ None)、
    `change_request_number`は版が反映した CR(CR を渡さない再生成の版は None)。"""

    doc_type: str
    version: int | None
    change_request_number: int | None
    elements: tuple[ElementStatus, ...]
    restored: tuple[RestoredElement, ...]
