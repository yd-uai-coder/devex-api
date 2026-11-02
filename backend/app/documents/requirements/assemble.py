"""AI の下書きから要件定義書の model を組み立てる(純粋関数)。

AI には要件の中身・出どころ・観点だけを書かせ、ID は書かせない(前の版の同じ要件の ID を
`prev_id`で指させるだけ)。ID と出どころの最終的な値はここで決定的に決める:

- ID: 前の版に在る同じ種別の ID だけを引き継ぐ。それ以外は種別ごとの次の番号。消えた ID は
  使い回さない(後の Phase で段階1の`req_ids`などが ID で要件を指すため)。
- 出どころ: 観点の表の状態を上限にする(`cap_source`)。AI が「ユーザー」と書いても、観点が
  未決なら「推測」にする。Phase 34 の「根拠の無い決定は取り込まない」と同じ考え方。
- 変更なし(`unchanged`): 前の版の同じ ID・同じ種別の行があれば、その行をそのまま使う
  (再生成の言い換えで要件が変わらないため。`app.documents.revision`)。出どころだけは今の観点の
  表で上限をかけ直す(観点が採用から未決に戻ったのに、写した行が「ユーザー」のまま残らないように)。
  ただし合意済みの行(`agreed_ids`)にはかけ直さない。合意の根拠は観点ではなく合意そのもので
  あるため(合意の形への戻しは app/agreements/protection.py)。
- 利用者の ID(`assign_user_ids`): 要件と同じく、AI が`prev_id`で指した前の版の ID を引き継ぐ。
  指していなければ、同じ名前、次に同じ場面の文の前の行が1つだけのとき、その行を引き継ぐ(名前を
  言い換えても、確認事項の欄が別の項目に移らないように)。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.documents.ids import carry_over_ids, initial_next_seq, seq_id
from app.documents.requirements.model import (
    PRIORITIES,
    REQUIREMENT_KINDS,
    USER_PREFIX,
    Priority,
    RequirementKind,
    RequirementRow,
    RequirementsModel,
    RequirementSource,
    UserScene,
)
from app.hearing import POINT_DEFS, PointState, Scale, is_point_key

_SOURCE_RANK: dict[RequirementSource, int] = {"assumed": 0, "adopted": 1, "user": 2}
_RANK_SOURCE: dict[int, RequirementSource] = {rank: s for s, rank in _SOURCE_RANK.items()}
# 観点の状態ごとの、出どころの上限
_STATUS_CAP: dict[str, RequirementSource] = {
    "decided": "user",
    "adopted": "adopted",
    "open": "assumed",
    "not_applicable": "assumed",
}
# 規模はほぼ必ず決まっているので、それだけを根拠に上限を上げない(どの要件にも付けられるため)。
# ただし制約(CON)の行は規模そのもの(公開か社内か)を述べるので、規模も根拠に数える
_NOT_GROUNDING: frozenset[str] = frozenset({"scale"})
_SCALE_GROUNDS_KINDS: frozenset[str] = frozenset({"CON"})
_NFR_CATEGORY: dict[str, str] = {
    d.key: d.ipa_category for d in POINT_DEFS if d.kind == "nonfunctional"
}
_OTHER_CATEGORY = "その他"
# 1.2(利用者と使う場面)の根拠の観点
USER_POINTS = ("users", "daily_flow")


@dataclass(frozen=True)
class RequirementDraft:
    """AI が下書きした要件1行(ID の決まる前)。"""

    kind: str
    text: str
    source: str
    points: tuple[str, ...] = ()
    priority: str = ""
    category: str = ""
    prev_id: str = ""
    unchanged: bool = False


@dataclass(frozen=True)
class UserSceneDraft:
    user: str
    scene: str
    source: str
    prev_id: str = ""


@dataclass(frozen=True)
class RequirementsDraft:
    """AI の下書き1回分(構造化出力から`drafting.to_draft`で作る)。"""

    purpose: str
    users: tuple[UserSceneDraft, ...] = ()
    in_scope: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    requirements: tuple[RequirementDraft, ...] = ()


def known_points(points: Iterable[str]) -> list[str]:
    """知らない観点のキーを除き、重複を除いた観点(順は保つ)。"""
    seen: list[str] = []
    for key in points:
        key = key.strip()
        if is_point_key(key) and key not in seen:
            seen.append(key)
    return seen


def grounding_points(points: Sequence[str], kind: str = "") -> list[str]:
    """行の根拠の観点(出どころの上限を決める観点)。規模は除く。ただし制約の行は規模も含む。"""
    excluded = frozenset() if kind in _SCALE_GROUNDS_KINDS else _NOT_GROUNDING
    return [key for key in points if key not in excluded]


def cap_source(
    source: str, points: Sequence[str], states: Mapping[str, PointState], *, kind: str = ""
) -> RequirementSource:
    """出どころに、観点の状態で上限をかける。

    上限は根拠の観点(規模を除く。制約の行は規模も含む)のうち最も強い状態から決まる(決定 →
    ユーザー、採用 → 採用、未決・対象外 → 推測)。根拠の観点が無い・知らない出どころなら推測。"""
    if source not in _SOURCE_RANK:
        return "assumed"
    caps = [
        _SOURCE_RANK[_STATUS_CAP[states[key].status]]
        for key in grounding_points(points, kind)
        if key in states
    ]
    if not caps:
        return "assumed"
    return _RANK_SOURCE[min(_SOURCE_RANK[source], max(caps))]  # type: ignore[index]


def assign_ids(
    kinds_and_prev: Sequence[tuple[RequirementKind, str]], previous: RequirementsModel | None
) -> tuple[list[str], dict[str, int]]:
    """行ごとの ID と、次の番号を決める(`app.documents.ids.carry_over_ids`)。

    `prev_id`が前の版に在り、種別が同じで、まだ使われていなければ引き継ぐ。それ以外(新しい要件・
    前の版に無い ID・種別違い・重複)は種別ごとの次の番号を振る。"""
    rows = previous.rows if previous else []
    next_seq = initial_next_seq(
        REQUIREMENT_KINDS, [r.id for r in rows], previous.next_seq if previous else {}
    )
    return carry_over_ids(kinds_and_prev, {r.id: r.kind for r in rows}, next_seq)


def assign_user_ids(
    drafts: Sequence[UserSceneDraft], previous: RequirementsModel | None
) -> tuple[list[str], int]:
    """利用者の行ごとの ID と、次の番号を決める。

    1段目で`prev_id`が前の版に在り、まだ使われていなければ引き継ぐ。2段目で、当たらなかった行は
    前の版の使われていない行のうち、同じ名前の行、無ければ同じ場面の文の行が1つだけのとき引き継ぐ。
    残りは次の番号(消えた番号は使い回さない)。"""
    prev_users = previous.users if previous else []
    seq = initial_next_seq(
        (USER_PREFIX,), [u.id for u in prev_users], previous.next_seq if previous else {}
    )[USER_PREFIX]
    unused = {u.id: u for u in prev_users if u.id}
    ids: list[str] = []
    for draft in drafts:
        prev_id = draft.prev_id.strip().upper()
        ids.append(prev_id if unused.pop(prev_id, None) is not None else "")
    for index, draft in enumerate(drafts):
        if ids[index]:
            continue
        for value_of in (lambda u: u.user, lambda u: u.scene):
            value = value_of(draft).strip()
            found = [u for u in unused.values() if value and value_of(u).strip() == value]
            if len(found) == 1:
                ids[index] = unused.pop(found[0].id).id
                break
    for index, user_id in enumerate(ids):
        if not user_id:
            ids[index] = seq_id(USER_PREFIX, seq)
            seq += 1
    return ids, seq


def _priority(value: str) -> Priority:
    return value if value in PRIORITIES else "Must"  # type: ignore[return-value]


def _category(kind: RequirementKind, category: str, points: Sequence[str]) -> str:
    """NFR は IPA の大項目(観点の名前)にそろえる。CON は AI の書いた種類のまま。"""
    category = category.strip()
    if kind != "NFR" or category in _NFR_CATEGORY.values():
        return category or _OTHER_CATEGORY
    for key in points:
        if key in _NFR_CATEGORY:
            return _NFR_CATEGORY[key]
    return category or _OTHER_CATEGORY


def build_model(
    draft: RequirementsDraft,
    previous: RequirementsModel | None,
    states: Mapping[str, PointState],
    scale: Scale | None,
    *,
    agreed_ids: frozenset[str] = frozenset(),
) -> RequirementsModel:
    """下書きから model を作る(知らない種別・空の要件は捨てる)。観点の状態と規模を写して持つ。
    `agreed_ids`は合意済みの要件の ID(`unchanged`で写すとき、出どころの上限をかけ直さない)。"""
    kept = [r for r in draft.requirements if r.kind in REQUIREMENT_KINDS and r.text.strip()]
    kinds: list[RequirementKind] = [r.kind for r in kept]  # type: ignore[misc]
    ids, next_seq = assign_ids(
        [(kind, r.prev_id) for kind, r in zip(kinds, kept, strict=True)], previous
    )
    prev_rows = {row.id: row for row in previous.rows} if previous else {}
    rows: list[RequirementRow] = []
    for req_id, kind, r in zip(ids, kinds, kept, strict=True):
        same = prev_rows.get(req_id)
        if r.unchanged and same is not None and same.kind == kind:
            if req_id in agreed_ids:
                rows.append(same)
                continue
            source = cap_source(same.source, same.points, states, kind=kind)
            rows.append(
                same if source == same.source else same.model_copy(update={"source": source})
            )
            continue
        points = known_points(r.points)
        rows.append(
            RequirementRow(
                id=req_id,
                kind=kind,
                priority=_priority(r.priority) if kind == "FR" else None,
                category="" if kind == "FR" else _category(kind, r.category, points),
                text=r.text.strip(),
                source=cap_source(r.source, points, states, kind=kind),
                points=points,
            )
        )
    drafted_users = [u for u in draft.users if u.user.strip()]
    user_ids, next_user_seq = assign_user_ids(drafted_users, previous)
    users = [
        UserScene(
            id=user_id,
            user=u.user.strip(),
            scene=u.scene.strip(),
            source=cap_source(u.source, USER_POINTS, states),
        )
        for user_id, u in zip(user_ids, drafted_users, strict=True)
    ]
    next_seq = {**next_seq, USER_PREFIX: next_user_seq}
    return RequirementsModel(
        purpose=draft.purpose.strip(),
        users=users,
        in_scope=[s.strip() for s in draft.in_scope if s.strip()],
        out_of_scope=[s.strip() for s in draft.out_of_scope if s.strip()],
        rows=rows,
        next_seq=next_seq,
        point_statuses={key: state.status for key, state in states.items()},
        scale=scale,
    )


def mark_adopted(model: RequirementsModel, req_id: str) -> RequirementsModel | None:
    """推測の要件を、利用者が確かめた「採用」にする。推測の行が無ければ None。"""
    target = next((r for r in model.rows if r.id == req_id and r.source == "assumed"), None)
    if target is None:
        return None
    rows = [r.model_copy(update={"source": "adopted"}) if r is target else r for r in model.rows]
    return model.model_copy(update={"rows": rows})


def mark_user_adopted(model: RequirementsModel, user_id: str) -> RequirementsModel | None:
    """推測の利用者と使う場面(1.2 の行。ID で指す)を「採用」にする。推測の行が無ければ None。"""
    target = next(
        (u for u in model.users if u.id == user_id.strip() and u.source == "assumed"), None
    )
    if target is None:
        return None
    users = [
        u.model_copy(update={"source": "adopted"}) if u is target else u for u in model.users
    ]
    return model.model_copy(update={"users": users})
