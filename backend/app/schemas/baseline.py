import uuid
from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, field_validator

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
    requirement / screen / api。"""

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
