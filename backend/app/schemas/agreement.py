from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

# 合意の記録の文の上限(相手は名前・会社名、メモは打ち合わせの要点)
COUNTERPART_MAX = 200
MEMO_MAX = 10000
REASON_MAX = 2000


class AgreementNotes(BaseModel):
    """合意の記録の共通の項目(合意済みの版の作成と、CR の合意・却下で同じ)。相手は空でもよい
    (空白だけは null にそろえる)。メモは空でもよく、理由は必須。日時は使う側で持つ
    (合意済みの版は`agreed_at`、CR は`decided_at`)。"""

    counterpart: str | None = Field(default=None, max_length=COUNTERPART_MAX)
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
