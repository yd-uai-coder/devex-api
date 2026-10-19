"""変更要求(CR)の対象(要件 ID と変更の種類)の整え方と保存の形(純粋関数)。

docs/external_design.md 2.14節。種類は「内容の変更」と「削除」。影響のたどり方は種類によらず
同じ(内容の変更として求める。反映の後の差分でも、消えた要素は内容の変更としてたどるため)で、
種類は対象の表示・説明書・予測の保存に載る。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

TargetKind = Literal["content", "delete"]

TARGET_KINDS: tuple[TargetKind, ...] = get_args(TargetKind)
TARGET_KIND_LABEL: dict[TargetKind, str] = {"content": "内容の変更", "delete": "削除"}


@dataclass(frozen=True)
class Target:
    """CR の対象1つ(基準の束の要件定義書にある要件 ID と、変更の種類)。"""

    requirement_id: str
    kind: TargetKind = "content"


def normalize_targets(targets: Iterable[Target]) -> list[Target]:
    """対象の要件 ID の前後の空白を除き、同じ組の重複を除く(最初に出た順)。空の ID は捨てる。
    同じ要件 ID が違う種類で現れたら ValueError(どちらの変更か決められないため)。"""
    kinds: dict[str, TargetKind] = {}
    for target in targets:
        req_id = target.requirement_id.strip()
        if not req_id:
            continue
        previous = kinds.setdefault(req_id, target.kind)
        if previous != target.kind:
            raise ValueError(f"同じ要件が違う種類で指定されています: {req_id}")
    return [Target(req_id, kind) for req_id, kind in kinds.items()]


def target_ids(targets: Iterable[Target]) -> list[str]:
    """対象の要件 ID(対象の順)。重なりと存在の確かめは ID だけで見る。"""
    return [target.requirement_id for target in targets]


def targets_to_json(targets: Iterable[Target]) -> list[dict[str, str]]:
    """保存の形(`[{requirement_id, kind}]`)。"""
    return [{"requirement_id": t.requirement_id, "kind": t.kind} for t in targets]


def targets_from_json(value: Sequence[Mapping[str, Any]] | None) -> list[Target]:
    """保存の形から読む。知らない種類は内容の変更とみなす。"""
    found = []
    for row in value or []:
        kind = row.get("kind")
        found.append(
            Target(str(row["requirement_id"]), kind if kind in TARGET_KINDS else "content")
        )
    return found
