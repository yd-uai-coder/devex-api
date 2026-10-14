"""設計レビューの構造化出力のスキーマ(レビューのモデルが返す形)。

レビューは設計を書き換えず、指摘だけを返す。指摘は重要度(#28 の3段階)・原因(ヒアリング /
設計 / 不整合)・対象・関連する文書(直すときに書き換える文書)・観点のキー(原因がヒアリングの
とき)・問題・引用・理由・改善案を持つ。
"""

from collections.abc import Collection
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.hearing import is_point_key

ReviewLevel = Literal["critical", "major", "minor"]
ReviewCause = Literal["hearing", "design", "inconsistency"]

REVIEW_LEVELS: tuple[ReviewLevel, ...] = ("critical", "major", "minor")
LEVEL_LABEL: dict[ReviewLevel, str] = {"critical": "最重要", "major": "中程度", "minor": "軽微"}
# 指摘が関連する文書(直すときに書き換える文書)。上流 → 下流の順。detailed_design・detailed_plan は
# 詳細設計モードの段階の区切りで渡す詳細設計書・実装計画書
REVIEW_DOCUMENTS: tuple[str, ...] = (
    "requirements",
    "external_design",
    "internal_design",
    "implementation_plan",
    "detailed_design",
    "detailed_plan",
)

CAUSE_LABEL: dict[ReviewCause, str] = {
    "hearing": "ヒアリング",
    "design": "設計",
    "inconsistency": "不整合",
}


class ReviewFindingOut(BaseModel):
    """レビューの指摘1件。"""

    level: ReviewLevel = Field(
        description="critical=決まらないと実装に着手できない / major=実装はできるが手戻りになりうる"
        " / minor=細部"
    )
    cause: ReviewCause = Field(
        description="hearing=ヒアリングで聞けていない・聞き方が浅い(利用者に聞き直さないと決め"
        "られない) / design=1つの文書の中の誤り・抜け / inconsistency=文書どうし・要件と設計の"
        "食い違い"
    )
    targets: list[str] = Field(
        description="対象の ID(FR-001・NFR-006・SCR-002・DF-3 など)や、ID の無いときは"
        "『POST /api/v1/projects』『テーブル users』のような文書の中の名前"
    )
    # 既定値を持たせない(省略できる項目にすると、モデルが書かないことがあるため)
    documents: list[str] = Field(
        description="指摘を直すときに書き換える文書の種類(上流から)。requirements=要件定義書 / "
        "external_design=外部設計書 / internal_design=内部設計書 / implementation_plan=実装計画書"
        " / detailed_design=詳細設計書 / detailed_plan=実装計画書(詳細設計)。対象がモジュール・"
        "API パス・テーブルなど設計の中の名前で、要件を変えずに直せるなら requirements を入れない"
    )
    # 既定値を持たせない(省略できる項目にすると、モデルが書かないことがあるため。null は書ける)
    point_key: str | None = Field(
        description="cause が hearing のとき、聞き直す観点のキー(観点の表の「キー」列。"
        "security・abuse など)。それ以外は null",
    )
    problem: str = Field(description="何が問題か(1〜2文)")
    evidence: str = Field(
        description="根拠になる文書の箇所の抜き書き(そのまま引用する。書かれていないことが"
        "問題のときは、その事実を決めるはずの箇所)"
    )
    reason: str = Field(description="なぜ問題か。根拠の文書・節を挙げる(1〜2文)")
    suggestion: str = Field(description="どう直すかの案(1〜2文)")

    @field_validator("documents")
    @classmethod
    def _known_documents(cls, value: list[str]) -> list[str]:
        """知らない文書の種類は捨て、重ねず、上流から並べる。"""
        return [d for d in REVIEW_DOCUMENTS if d in value]

    @field_validator("point_key")
    @classmethod
    def _known_point_key(cls, value: str | None) -> str | None:
        """観点の表に無いキーは null にする(聞き直す観点を作り出さない)。"""
        return value if value is not None and is_point_key(value) else None


class ReviewOutput(BaseModel):
    """レビューの結果(指摘の一覧。指摘が無ければ空)。"""

    findings: list[ReviewFindingOut]


def only_reviewed_documents(
    findings: list[ReviewFindingOut], reviewed: Collection[str]
) -> list[ReviewFindingOut]:
    """指摘の関連文書を、その回にレビューした文書だけに絞る(モデルが渡していない文書を挙げることが
    ある。詳細設計モードの段階の区切りで内部設計書を挙げる、など)。"""
    return [
        f.model_copy(update={"documents": [d for d in f.documents if d in reviewed]})
        for f in findings
    ]
