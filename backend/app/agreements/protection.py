"""合意済みの要素を、再生成の後に合意の形へ戻す(純粋関数)。

docs/external_design.md 2.15節「合意済みの行の保護」。LLM には合意済みの行を変えないよう頼むが、
約束だけでは揺れを止められないので、組み立ての後にコードで戻す。

- 要件の行(`req:<ID>`): 中身(すべての欄)が合意と違えば合意の中身に置き換え、落ちていれば戻す。
  戻す位置は、前の版で同じ種別の直前にあった行の後(並びが再生成の前後で変わらないように)。
- 利用者と使う場面(`users`): 1.2 全体を1つの要素として比べ、違えば合意の形に置き換える。
- 合意済みの行と同じ文の新しい行(言い換えずに ID だけ変えた重複)は除く。
- 書き換えてよい要素(反映する CR の対象)は`exempt`で受け、戻さない。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from app.agreements.items import USERS_KEY, body_of
from app.documents.ids import parse_seq_id
from app.documents.requirements.model import (
    REQUIREMENT_KINDS,
    RequirementRow,
    RequirementsModel,
    UserScene,
)

RestoreReason = Literal["changed", "dropped"]

RESTORE_REASONS: tuple[RestoreReason, ...] = get_args(RestoreReason)

_REQ_PREFIX = "req:"


@dataclass(frozen=True)
class Restored:
    """戻した要素1つ(`changed` = LLM が書き換えた、`dropped` = LLM が落とした)。"""

    key: str
    reason: RestoreReason


def requirement_key(req_id: str) -> str:
    return f"{_REQ_PREFIX}{req_id}"


def agreed_requirement_ids(agreed: Mapping[str, Any], exempt: frozenset[str]) -> frozenset[str]:
    """守る要件の ID(合意済みで、書き換えてよい要素に入らないもの)。"""
    return frozenset(
        key.removeprefix(_REQ_PREFIX)
        for key in agreed
        if key.startswith(_REQ_PREFIX) and key not in exempt
    )


def protect_requirements(
    model: RequirementsModel,
    previous: RequirementsModel | None,
    agreed: Mapping[str, dict[str, Any]],
    *,
    exempt: frozenset[str] = frozenset(),
) -> tuple[RequirementsModel, list[Restored]]:
    """組み立てた model の、合意済みの要件の行と利用者(1.2)を合意の形に戻す。

    `agreed`は今の合意(キー → 合意した中身)。要件と利用者のキーだけを読む。戻した要素を、
    要件(合意の順)→ 利用者の順に返す。合意が無ければ model をそのまま返す。"""
    restored: list[Restored] = []
    agreed_rows = {
        key: RequirementRow.model_validate(content)
        for key, content in agreed.items()
        if key.startswith(_REQ_PREFIX) and key not in exempt
    }
    rows = list(model.rows)
    index = {requirement_key(row.id): i for i, row in enumerate(rows)}
    dropped: list[RequirementRow] = []
    for key, row in agreed_rows.items():
        at = index.get(key)
        if at is None:
            dropped.append(row)
            restored.append(Restored(key, "dropped"))
        elif body_of("requirement", rows[at].model_dump(mode="json")) != body_of(
            "requirement", row.model_dump(mode="json")
        ):
            rows[at] = row
            restored.append(Restored(key, "changed"))
    agreed_texts = {row.text.strip() for row in agreed_rows.values()}
    rows = [
        row
        for row in rows
        if requirement_key(row.id) in agreed_rows or row.text.strip() not in agreed_texts
    ]
    for row in _in_previous_order(dropped, previous):
        rows.insert(_insert_at(rows, row, previous), row)

    users = model.users
    agreed_users = agreed.get(USERS_KEY)
    if agreed_users is not None and USERS_KEY not in exempt:
        current = {"users": [u.model_dump(mode="json") for u in model.users]}
        if body_of("users", current) != body_of("users", agreed_users):
            users = [UserScene.model_validate(u) for u in agreed_users["users"]]
            restored.append(Restored(USERS_KEY, "dropped" if not model.users else "changed"))

    if not restored and len(rows) == len(model.rows):
        return model, restored
    update = {"rows": rows, "users": users, "next_seq": _next_seq(model.next_seq, dropped)}
    return model.model_copy(update=update), restored


def _in_previous_order(
    rows: list[RequirementRow], previous: RequirementsModel | None
) -> list[RequirementRow]:
    """前の版の並びの順(前の版に無い行は後ろ。その中は合意の順)。続けて戻す行が、前に戻した
    行の後ろにつながるように。"""
    order = {row.id: i for i, row in enumerate(previous.rows)} if previous else {}
    return sorted(rows, key=lambda row: order.get(row.id, len(order)))


def _insert_at(
    rows: list[RequirementRow], row: RequirementRow, previous: RequirementsModel | None
) -> int:
    """落ちた行を戻す位置。前の版で同じ種別の直前にあった行(今もあるもの)の後。前の版で同じ種別の
    先頭なら、今の同じ種別の先頭。前の版に無ければ同じ種別の末尾。同じ種別が無ければ末尾。"""
    present = {r.id: i for i, r in enumerate(rows)}
    same_kind = [i for i, r in enumerate(rows) if r.kind == row.kind]
    before = [r for r in previous.rows if r.kind == row.kind] if previous else []
    ids = [r.id for r in before]
    if row.id in ids:
        for prev in reversed(ids[: ids.index(row.id)]):
            if prev in present:
                return present[prev] + 1
        return same_kind[0] if same_kind else len(rows)
    return same_kind[-1] + 1 if same_kind else len(rows)


def _next_seq(next_seq: Mapping[str, int], restored: list[RequirementRow]) -> dict[str, int]:
    """戻した行の番号を、次に振る番号が越えるようにする(消えた ID を使い回さないため)。"""
    seq = dict(next_seq)
    for row in restored:
        parsed = parse_seq_id(row.id, REQUIREMENT_KINDS)
        if parsed is not None:
            kind, number = parsed
            seq[kind] = max(seq.get(kind, 1), number + 1)
    return seq
