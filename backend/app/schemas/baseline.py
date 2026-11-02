import uuid
from datetime import datetime
from typing import Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, field_validator

from app.change_requests import Freshness
from app.schemas.agreement import AgreementNotes


class BaselineCreate(AgreementNotes):
    """合意済みの版(束)を作るときの合意の記録。相手・メモ・理由は AgreementNotes。合意の日時は
    記録する日時と別に、タイムゾーン付きで受け取る。`change_request_numbers`は、この束に入れる
    検証済みの CR の番号(任意。束と CR を結び、CR の時系列に1行残す)。"""

    agreed_at: AwareDatetime
    change_request_numbers: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("change_request_numbers")
    @classmethod
    def _unique_numbers(cls, value: list[int]) -> list[int]:
        if any(number < 1 for number in value):
            raise ValueError("CR の番号は1以上です")
        return list(dict.fromkeys(value))


class BaselineItemSummaryRead(BaseModel):
    """束の写し1件の要約(中身なし)。`key`は文書の種類か`stage:N`、`state`は段階の状態
    (StageState)か文書の`present`/`absent`。"""

    model_config = ConfigDict(from_attributes=True)

    key: str
    included: bool
    state: str
    source_version: int | None
    generated_model: str | None


class BaselineItemRead(BaselineItemSummaryRead):
    """束の写し1件(文書の md と意味モデル、段階の承認の写し)。"""

    content: str | None
    model: dict[str, Any] | None


class BaselineChangeRequestRead(BaseModel):
    """束に入れた CR 1つ(番号と題)。"""

    number: int
    title: str


class _BaselineRecordRead(BaseModel):
    """合意済みの版1つの番号と合意の記録(写しは継承先で持つ)。`label`は画面に出す名前(B-1…)。"""

    model_config = ConfigDict(from_attributes=True)

    number: int
    counterpart: str | None
    agreed_at: datetime
    memo: str
    reason: str
    recorded_by: uuid.UUID | None
    created_at: datetime
    # 束に入れた CR(番号の順)。ORM の行には無いので、ルートが`with_included`で足す
    included_change_requests: list[BaselineChangeRequestRead] = Field(default_factory=list)
    # 新しいか(最新の束で、束の後に反映した CR も古かった段階も無い)。ルートが`with_fresh`で足す
    # (足していなければ None)
    fresh: bool | None = None

    def with_included(self, included: list[tuple[int, str]]) -> Self:
        """束に入れた CR(`(番号, 題)`の一覧)を足した写し。"""
        return self.model_copy(
            update={
                "included_change_requests": [
                    BaselineChangeRequestRead(number=n, title=t) for n, t in included
                ]
            }
        )

    def with_fresh(self, fresh: bool) -> Self:
        """新しいかを足した写し。"""
        return self.model_copy(update={"fresh": fresh})

    @computed_field
    @property
    def label(self) -> str:
        return f"B-{self.number}"


class BaselineSummaryRead(_BaselineRecordRead):
    """合意済みの版1つ(一覧用。写しは要約だけ)。"""

    items: list[BaselineItemSummaryRead]


class BaselineRead(_BaselineRecordRead):
    """合意済みの版1つ(写しの中身付き)。"""

    items: list[BaselineItemRead]


class BaselineOriginRead(BaseModel):
    """束で、新しい要件の予測の起点に選べる要素1つ(CR の起点を選ぶ画面用)。`kind`は
    requirement / screen / api / function。"""

    key: str
    kind: str
    label: str


class BaselineRequirementRead(BaseModel):
    """束の要件定義書の写しにある要件1行(CR の対象を選ぶ画面用。根拠の観点などは返さない)。"""

    id: str
    kind: str
    priority: str | None = None
    category: str = ""
    text: str


class BaselineAppliedAfterRead(BaseModel):
    """束の後に反映した CR 1つと、それが書き換えた要素のキー(`req:FR-001`など)。"""

    number: int
    keys: list[str]


class BaselineFreshnessRead(BaseModel):
    """合意済みの版1つの鮮度。`fresh`は最新の束で、束の後に反映した CR も古かった段階も無いこと。
    `newer_numbers`はこの束より後の束、`applied_after`は束の後に反映した CR(反映の順)、
    `outdated_stages`は束を作った時点で承認のままでなかった段階、`tracked`は束の後の CR を判定
    できたか(合意の記録を始める前の束は False)。"""

    baseline_number: int
    fresh: bool
    is_latest: bool
    tracked: bool
    newer_numbers: list[int]
    applied_after: list[BaselineAppliedAfterRead]
    outdated_stages: list[int]

    @classmethod
    def of(cls, freshness: Freshness) -> Self:
        return cls(
            baseline_number=freshness.baseline_number,
            fresh=freshness.fresh,
            is_latest=freshness.is_latest,
            tracked=freshness.tracked,
            newer_numbers=list(freshness.newer_numbers),
            applied_after=[
                BaselineAppliedAfterRead(number=a.number, keys=list(a.keys))
                for a in freshness.applied_after
            ],
            outdated_stages=list(freshness.outdated_stages),
        )

    @computed_field
    @property
    def label(self) -> str:
        return f"B-{self.baseline_number}"
