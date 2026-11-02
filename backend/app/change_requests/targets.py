"""変更要求(CR)の対象(要件 ID・変更の種類・変更後の案)の整え方と保存の形(純粋関数)。

docs/external_design.md 2.14節。種類は「内容の変更」と「削除」。影響のたどり方は種類によらず
同じ(内容の変更として求める。反映の後の差分でも、消えた要素は内容の変更としてたどるため)で、
種類は対象の表示・説明書・予測の保存に載る。変更後の案(要件の新しい文の案)は任意で、説明書の
「合意済みの文と変更後の案」に載る。削除の対象は案を持たない。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

TargetKind = Literal["content", "delete"]

TARGET_KINDS: tuple[TargetKind, ...] = get_args(TargetKind)
TARGET_KIND_LABEL: dict[TargetKind, str] = {"content": "内容の変更", "delete": "削除"}


@dataclass(frozen=True)
class Target:
    """CR の対象1つ(基準の束の要件定義書にある要件 ID・変更の種類・変更後の案。案は空でもよい)。"""

    requirement_id: str
    kind: TargetKind = "content"
    proposed_text: str = ""


def normalize_targets(targets: Iterable[Target]) -> list[Target]:
    """対象の要件 ID と案の前後の空白を除き、同じ要件の重複を1つにまとめる(最初に出た順)。
    空の ID は捨てる。次のときは ValueError(どちらの変更か決められないため)。
    - 同じ要件 ID が違う種類で現れた
    - 同じ要件 ID に違う案が書かれた(片方が空なら、書かれた方を残す)
    - 削除の対象に案が書かれた"""
    found: dict[str, Target] = {}
    for target in targets:
        req_id = target.requirement_id.strip()
        if not req_id:
            continue
        current = Target(req_id, target.kind, target.proposed_text.strip())
        if current.kind == "delete" and current.proposed_text:
            raise ValueError(f"削除の対象には変更後の案を書けません: {req_id}")
        previous = found.get(req_id)
        if previous is None:
            found[req_id] = current
            continue
        if previous.kind != current.kind:
            raise ValueError(f"同じ要件が違う種類で指定されています: {req_id}")
        if not current.proposed_text or current.proposed_text == previous.proposed_text:
            continue
        if previous.proposed_text:
            raise ValueError(f"同じ要件に違う変更後の案が書かれています: {req_id}")
        found[req_id] = current
    return list(found.values())


def target_ids(targets: Iterable[Target]) -> list[str]:
    """対象の要件 ID(対象の順)。重なりと存在の確かめは ID だけで見る。"""
    return [target.requirement_id for target in targets]


def targets_to_json(targets: Iterable[Target]) -> list[dict[str, str]]:
    """保存の形(`[{requirement_id, kind, proposed_text}]`)。"""
    return [
        {"requirement_id": t.requirement_id, "kind": t.kind, "proposed_text": t.proposed_text}
        for t in targets
    ]


def targets_from_json(value: Sequence[Mapping[str, Any]] | None) -> list[Target]:
    """保存の形から読む。知らない種類は内容の変更、案の無い行は空の案とみなす。"""
    found = []
    for row in value or []:
        kind = row.get("kind")
        found.append(
            Target(
                str(row["requirement_id"]),
                kind if kind in TARGET_KINDS else "content",
                str(row.get("proposed_text") or ""),
            )
        )
    return found
