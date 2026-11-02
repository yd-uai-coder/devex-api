"""要件定義書の意味モデル(`generated_documents.model`に JSON で持つ)。

要件は FR(機能)・NFR(非機能)・CON(制約)の行。ID は種別ごとの通し番号(`FR-001`)で、
再生成しても同じ要件は同じ ID を引き継ぐ(`assemble.assign_ids`)。消えた ID は使い回さない
ので、種別ごとに次の番号(`next_seq`)を持つ。

1.2 の利用者と使う場面の行にも ID(`U-001`)を振る(確認事項のキーが名前に頼らないように)。
引き継ぎは`assemble.assign_user_ids`。ID の無い利用者(ID を振る前に保存した版など)には、
読むときに並び順で番号を振る(`number_users`。同じ版は読むたびに同じ ID になる)。md には出さない。
"""

from collections.abc import Sequence
from typing import Literal, Self, get_args

from pydantic import BaseModel, Field, model_validator

from app.documents.ids import parse_seq_id, seq_id

RequirementKind = Literal["FR", "NFR", "CON"]
RequirementSource = Literal["user", "adopted", "assumed"]
Priority = Literal["Must", "Should", "Could", "Won't"]

REQUIREMENT_KINDS: tuple[RequirementKind, ...] = get_args(RequirementKind)
PRIORITIES: tuple[Priority, ...] = get_args(Priority)
SOURCE_LABEL: dict[RequirementSource, str] = {
    "user": "ユーザー",
    "adopted": "採用",
    "assumed": "推測",
}


class RequirementRow(BaseModel):
    """要件1行。`points`は根拠の観点のキー(app/hearing/points.py)。"""

    id: str
    kind: RequirementKind
    priority: Priority | None = None  # FR だけ(MoSCoW)
    category: str = ""  # NFR は IPA の大項目、CON は種類(技術スタック・体制・期間など)
    text: str
    source: RequirementSource
    points: list[str] = Field(default_factory=list)


# 利用者と使う場面の行の ID の種別
USER_PREFIX = "U"


class UserScene(BaseModel):
    """1.2 の行(利用者と使う場面)。`id`は`U-001`の形(空 = まだ振っていない)。"""

    id: str = ""
    user: str
    scene: str
    source: RequirementSource


class RequirementsModel(BaseModel):
    """要件定義書1版の中身。1.1〜1.3 は本文と短い行、1.4〜1.6 は要件の行。"""

    purpose: str
    users: list[UserScene] = Field(default_factory=list)
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    rows: list[RequirementRow] = Field(default_factory=list)
    # 生成した時点の観点の状態(キー → 状態)と規模。付録・検証は今の表でなくこれを読む
    # (推測を「採用」にして組み立て直しても、付録が変わらないように)
    point_statuses: dict[str, str] = Field(default_factory=dict)
    scale: str | None = None
    # 種別ごとの次の番号(消えた ID を使い回さないため。利用者は`USER_PREFIX`)
    next_seq: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _number_users(self) -> Self:
        """ID の無い利用者に番号を振る(すべて振ってあれば何もしない)。"""
        if all(u.id for u in self.users):
            return self
        self.users, self.next_seq = number_users(self.users, self.next_seq)
        return self


def number_users(
    users: Sequence[UserScene],
    next_seq: dict[str, int],
    known: Sequence[UserScene] = (),
) -> tuple[list[UserScene], dict[str, int]]:
    """ID の無い利用者に ID を振る。`known`(今の版・前の版の利用者)に同じ名前の行があり、その
    ID がまだ使われていなければ引き継ぎ、それ以外は次の番号。次の番号は、使った ID を越える。"""
    used = {u.id for u in users if u.id}
    seq = max(
        [next_seq.get(USER_PREFIX, 1)]
        + [n + 1 for u in [*users, *known] if (n := _user_number(u.id)) is not None]
    )
    out: list[UserScene] = []
    for user in users:
        user_id = user.id
        if not user_id:
            same = next(
                (
                    k.id
                    for k in known
                    if k.id and k.id not in used and k.user.strip() == user.user.strip()
                ),
                "",
            )
            if same:
                user_id = same
            else:
                user_id = seq_id(USER_PREFIX, seq)
                seq += 1
            used.add(user_id)
        out.append(user if user_id == user.id else user.model_copy(update={"id": user_id}))
    return out, {**next_seq, USER_PREFIX: seq}


def _user_number(user_id: str) -> int | None:
    parsed = parse_seq_id(user_id, (USER_PREFIX,))
    return parsed[1] if parsed is not None else None


def requirement_id(kind: RequirementKind, seq: int) -> str:
    return seq_id(kind, seq)


def parse_requirement_id(value: str) -> tuple[RequirementKind, int] | None:
    """`FR-001`を(種別, 番号)にする。形が違えば None。"""
    return parse_seq_id(value, REQUIREMENT_KINDS)  # type: ignore[return-value]
