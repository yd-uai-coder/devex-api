import uuid
from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, computed_field

from app.schemas.agreement import AgreementNotes


class BaselineCreate(AgreementNotes):
    """合意済みの版(束)を作るときの合意の記録。相手・メモ・理由は AgreementNotes。合意の日時は
    記録する日時と別に、タイムゾーン付きで受け取る。"""

    agreed_at: AwareDatetime


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
