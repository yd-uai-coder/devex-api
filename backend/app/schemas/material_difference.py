import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, computed_field, model_validator

from app.materials import DifferenceKind, DifferenceStatus
from app.schemas.baseline import BaselineFreshnessRead

DISMISS_REASON_MAX = 500


class MaterialDifferenceRead(BaseModel):
    """資料から取り出した違い1件。`reasons`は検査で弾いた理由(弾いたときだけ)、`dismiss_reason`は
    捨てた理由。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: DifferenceKind
    target_key: str
    origin_key: str
    content: str
    quote: str
    status: DifferenceStatus
    reasons: list[str]
    dismiss_reason: str | None
    baseline_number: int
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    created_at: datetime


class MaterialExtractionRead(BaseModel):
    """取り出しの直近の回(比べた束と、その時点の束の鮮度・モデル・日時)。"""

    model_config = ConfigDict(from_attributes=True)

    baseline_number: int
    freshness: BaselineFreshnessRead
    generated_model: str | None
    generated_by: uuid.UUID | None
    generated_at: datetime

    @computed_field
    @property
    def baseline_label(self) -> str:
        return f"B-{self.baseline_number}"


class MaterialDifferencesRead(BaseModel):
    """資料1件の違いの一覧。`extraction`はまだ取り出していなければ None。`latest_baseline_number`は
    今の最新の束(取り出した後に束を作り直したかを見るため。束が無ければ None)。"""

    material_id: uuid.UUID
    extraction: MaterialExtractionRead | None
    latest_baseline_number: int | None
    differences: list[MaterialDifferenceRead]


class MaterialDifferenceUpdate(BaseModel):
    """違いを捨てる(理由が要る)・捨てたものを未確認に戻す。"""

    action: Literal["dismiss", "restore"]
    reason: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=DISMISS_REASON_MAX),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def _reason_for_dismiss(self) -> Self:
        if self.action == "dismiss" and self.reason is None:
            raise ValueError("捨てるときは理由を書いてください")
        return self
