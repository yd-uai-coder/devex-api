import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

# 資料の種類(議事録・メール・資料)と、本文の出どころ(貼り付け・ファイルの形式)
MaterialKind = Literal["minutes", "email", "document"]
MaterialSource = Literal["paste", "txt", "md", "pdf"]

MATERIAL_TITLE_MAX = 200

MaterialTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MATERIAL_TITLE_MAX)
]


class MaterialMeta(BaseModel):
    """資料の種類・題・日付(貼り付けでもファイルでも同じ)。"""

    kind: MaterialKind
    title: MaterialTitle
    material_date: date


class MaterialCreate(MaterialMeta):
    """本文を貼り付けて資料を足すとき。本文の上限の文字数はサービスで確かめる(ファイルから取り出した
    本文と同じ誤りにするため)。"""

    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class MaterialSummaryRead(BaseModel):
    """資料1件の要約(本文なし。`length`は本文の文字数)。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: MaterialKind
    title: str
    material_date: date
    source: MaterialSource
    filename: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    length: int


class MaterialRead(MaterialSummaryRead):
    """資料1件(本文つき)。"""

    body: str
