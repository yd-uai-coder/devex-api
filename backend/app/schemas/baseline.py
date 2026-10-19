import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
)

# 合意の記録の文の上限(相手は名前・会社名、メモは打ち合わせの要点)
COUNTERPART_MAX = 200
MEMO_MAX = 10000
REASON_MAX = 2000


class BaselineCreate(BaseModel):
    """合意済みの版(束)を作るときの合意の記録。相手は空でもよい(空白だけは null にそろえる)。
    合意の日時は記録する日時と別に、タイムゾーン付きで受け取る。理由は必須。"""

    counterpart: str | None = Field(default=None, max_length=COUNTERPART_MAX)
    agreed_at: AwareDatetime
    memo: str = Field(default="", max_length=MEMO_MAX)
    reason: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=REASON_MAX)
    ]

    @field_validator("counterpart")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


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
