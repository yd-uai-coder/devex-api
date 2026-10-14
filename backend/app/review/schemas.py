"""設計レビューの構造化出力のスキーマ(レビューのモデルが返す形)。

レビューは設計を書き換えず、指摘だけを返す。指摘は重要度(#28 の3段階)・原因(ヒアリング /
設計 / 不整合)・対象・観点のキー(原因がヒアリングのとき)・問題・引用・理由・改善案を持つ。
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.hearing import is_point_key

ReviewLevel = Literal["critical", "major", "minor"]
ReviewCause = Literal["hearing", "design", "inconsistency"]

REVIEW_LEVELS: tuple[ReviewLevel, ...] = ("critical", "major", "minor")
LEVEL_LABEL: dict[ReviewLevel, str] = {"critical": "最重要", "major": "中程度", "minor": "軽微"}
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

    @field_validator("point_key")
    @classmethod
    def _known_point_key(cls, value: str | None) -> str | None:
        """観点の表に無いキーは null にする(聞き直す観点を作り出さない)。"""
        return value if value is not None and is_point_key(value) else None


class ReviewOutput(BaseModel):
    """レビューの結果(指摘の一覧。指摘が無ければ空)。"""

    findings: list[ReviewFindingOut]
