"""要件定義書の意味モデル(`generated_documents.model`に JSON で持つ)。

要件は FR(機能)・NFR(非機能)・CON(制約)の行。ID は種別ごとの通し番号(`FR-001`)で、
再生成しても同じ要件は同じ ID を引き継ぐ(`assemble.assign_ids`)。消えた ID は使い回さない
ので、種別ごとに次の番号(`next_seq`)を持つ。
"""

from typing import Literal, get_args

from pydantic import BaseModel, Field

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


class UserScene(BaseModel):
    """1.2 の行(利用者と使う場面)。"""

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
    # 種別ごとの次の番号(消えた ID を使い回さないため)
    next_seq: dict[str, int] = Field(default_factory=dict)


def requirement_id(kind: RequirementKind, seq: int) -> str:
    return f"{kind}-{seq:03d}"


def parse_requirement_id(value: str) -> tuple[RequirementKind, int] | None:
    """`FR-001`を(種別, 番号)にする。形が違えば None。"""
    kind, sep, number = value.strip().partition("-")
    if not sep or kind not in REQUIREMENT_KINDS or not number.isdigit():
        return None
    return kind, int(number)  # type: ignore[return-value]
