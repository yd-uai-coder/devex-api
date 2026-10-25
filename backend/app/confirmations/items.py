"""確認事項の一覧の項目(推測の要件・推測の利用者・根拠が未決の観点)の導出と、推測の採用率
(純粋関数)。

docs/external_design.md 2.15節。一覧は、今の要件定義書の model と観点の表から毎回決定的に
組み立てる(LLM なし)。利用者が決めた欄(決める機会・状態・メモ)だけを、項目のキーごとに保存する
(app/models/confirmation.py)。項目のキーは次のとおり。

- 推測の要件: `req:<要件 ID>`(合意の記録のキーと同じ)
- 推測の利用者(1.2 の行): `user:<利用者の ID>`(`U-001`。名前を言い換えても同じ項目になる)。
  ID を振る前の記録のキー(`user:<利用者の名前>`)は、`legacy_user_keys`で今の行の ID のキーに
  付け直す
- 根拠が未決の観点: `point:<観点のキー>`

CR の反映で文が変わった推測の要件は、確認済みでも未確認に戻す(`changed_requirement_keys`。
戻した CR の番号を記録に残す)。

観点の状態はチャットと観点の表で決める。一覧は観点を読むだけで、観点が決まれば次の組み立てで
消える。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from app.documents.requirements.assemble import USER_POINTS, grounding_points
from app.documents.requirements.model import RequirementRow, RequirementsModel, UserScene
from app.hearing import POINT_KEYS, PointState, point_def

ConfirmationKind = Literal["requirement", "user", "point"]
ConfirmationStatus = Literal["unconfirmed", "confirmed", "rejected"]
Opportunity = Literal["next_meeting", "email", "implementation", "none"]

CONFIRMATION_KINDS: tuple[ConfirmationKind, ...] = get_args(ConfirmationKind)
CONFIRMATION_STATUSES: tuple[ConfirmationStatus, ...] = get_args(ConfirmationStatus)
OPPORTUNITIES: tuple[Opportunity, ...] = get_args(Opportunity)
STATUS_LABEL: dict[ConfirmationStatus, str] = {
    "unconfirmed": "未確認",
    "confirmed": "確認済み",
    "rejected": "却下",
}
OPPORTUNITY_LABEL: dict[Opportunity, str] = {
    "next_meeting": "次の打ち合わせ",
    "email": "メール",
    "implementation": "実装中",
    "none": "決めない",
}
# 推測の採用率の対象(推測の行。観点は行ではないので数えない)
RATED_KINDS: frozenset[ConfirmationKind] = frozenset({"requirement", "user"})

_KEY_PREFIXES: dict[ConfirmationKind, str] = {
    "requirement": "req:",
    "user": "user:",
    "point": "point:",
}


@dataclass(frozen=True)
class ConfirmationItem:
    """確認事項1つ。`refs`は観点に頼っている要件の ID(1.2 は`1.2`。要件・利用者は空)。"""

    key: str
    kind: ConfirmationKind
    label: str
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdoptionCounts:
    """推測の採用率。`rate`は 確認済み ÷(確認済み + 却下)。分母が0なら None。"""

    confirmed: int
    rejected: int
    unconfirmed: int

    @property
    def rate(self) -> float | None:
        decided = self.confirmed + self.rejected
        return self.confirmed / decided if decided else None


def requirement_item_key(req_id: str) -> str:
    return f"{_KEY_PREFIXES['requirement']}{req_id}"


def user_item_key(user_id: str) -> str:
    return f"{_KEY_PREFIXES['user']}{user_id.strip()}"


def point_item_key(point_key: str) -> str:
    return f"{_KEY_PREFIXES['point']}{point_key}"


def requirement_item(row: RequirementRow) -> ConfirmationItem:
    """推測の要件の項目(表示名は ID と文)。"""
    return ConfirmationItem(requirement_item_key(row.id), "requirement", f"{row.id} {row.text}")


def user_item(scene: UserScene) -> ConfirmationItem:
    """推測の利用者の項目(キーは行の ID、表示名は利用者と使う場面)。"""
    return ConfirmationItem(user_item_key(scene.id), "user", f"{scene.user.strip()}: {scene.scene}")


def legacy_user_keys(model: RequirementsModel | None) -> dict[str, str]:
    """ID を振る前の利用者の記録のキー(`user:<名前>`)→ 今の行の ID のキー。同じ名前の行が
    2つあれば先の行。model が無ければ空。"""
    if model is None:
        return {}
    keys: dict[str, str] = {}
    for scene in model.users:
        name = scene.user.strip()
        if name:
            keys.setdefault(f"{_KEY_PREFIXES['user']}{name}", user_item_key(scene.id))
    return keys


def confirmation_kind_of(key: str) -> ConfirmationKind | None:
    """項目のキーの種類(形の違うキーは None)。"""
    return next(
        (k for k, prefix in _KEY_PREFIXES.items() if key.startswith(prefix) and key[len(prefix) :]),
        None,
    )


def item_target(key: str) -> str:
    """項目のキーが指すもの(要件 ID・利用者の ID・観点のキー)。"""
    kind = confirmation_kind_of(key)
    return key[len(_KEY_PREFIXES[kind]) :] if kind is not None else key


def confirmation_items(
    model: RequirementsModel | None, states: Mapping[str, PointState]
) -> list[ConfirmationItem]:
    """今の要件定義書の model と観点の表から、確認事項を並べる(推測の要件〈文書の順〉→ 推測の
    利用者 → 根拠が未決の観点〈観点の表の順〉)。

    根拠の観点は、要件の行の`points`(出どころの上限と同じ規則。規模は制約の行だけ)と、1.2 に
    行があるときの利用者・日々の流れの観点。観点の表に行が無い観点は未決とみなす。model が無い
    (要件定義書がまだ無い)ときは空。"""
    if model is None:
        return []
    items = [requirement_item(row) for row in model.rows if row.source == "assumed"]
    seen_users: set[str] = set()
    for scene in model.users:
        if scene.source == "assumed" and scene.user.strip() and scene.id not in seen_users:
            seen_users.add(scene.id)
            items.append(user_item(scene))
    refs: dict[str, list[str]] = {}
    for row in model.rows:
        for key in grounding_points(row.points, row.kind):
            refs.setdefault(key, []).append(row.id)
    if model.users:
        for key in USER_POINTS:
            refs.setdefault(key, []).append("1.2")
    for key in POINT_KEYS:
        state = states.get(key)
        if key in refs and (state is None or state.status == "open"):
            label = point_def(key).label
            refs_of = tuple(dict.fromkeys(refs[key]))
            items.append(ConfirmationItem(point_item_key(key), "point", label, refs_of))
    return items


def changed_requirement_keys(
    previous: RequirementsModel | None,
    new: RequirementsModel | None,
    requirement_ids: Iterable[str],
) -> list[str]:
    """CR の対象の要件のうち、反映の前後の要件定義書で文が変わった行の項目のキー(対象の順)。
    前の版に無い行(新しい要件)と、反映で消えた行は含めない(消えた行は一覧に出ないので、
    確認の記録はそのまま残す)。"""
    before = {row.id: row.text for row in previous.rows} if previous else {}
    after = {row.id: row.text for row in new.rows} if new else {}
    return [
        requirement_item_key(req_id)
        for req_id in dict.fromkeys(requirement_ids)
        if req_id in before and req_id in after and before[req_id] != after[req_id]
    ]


def adoption_counts(
    entries: Iterable[tuple[ConfirmationKind, ConfirmationStatus, bool]],
) -> AdoptionCounts:
    """推測の採用率を数える。`entries`は項目ごとの(種類・状態・今の一覧にあるか)。確認済み・却下は
    今の一覧から消えたもの(採用にして推測でなくなった行など)も数え、未確認は今の一覧にあるものだけ
    数える。観点は数えない。"""
    confirmed = rejected = unconfirmed = 0
    for kind, status, present in entries:
        if kind not in RATED_KINDS:
            continue
        if status == "confirmed":
            confirmed += 1
        elif status == "rejected":
            rejected += 1
        elif present:
            unconfirmed += 1
    return AdoptionCounts(confirmed, rejected, unconfirmed)
