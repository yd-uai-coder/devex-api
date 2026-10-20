"""合意の記録(ステージ12)の純粋関数。

docs/internal_design.md 3.3節「12. 合意の精度」。

- items: 合意の対象の要素(要件・利用者と使う場面・画面・API・外部連携)と比べる値
- origins: 合意の出どころと、画面に出す名前
- protection: 再生成の後に、合意済みの要素を合意の形へ戻す
"""

from app.agreements.items import (
    AGREED_KINDS,
    USERS_KEY,
    USERS_LABEL,
    AgreedItem,
    AgreedKind,
    agreement_items,
    body_of,
    integration_key,
    removed_keys,
)
from app.agreements.origins import (
    ADOPT_LABEL,
    AGREEMENT_ORIGINS,
    AgreementOrigin,
    origin_label,
)
from app.agreements.protection import (
    RESTORE_REASONS,
    Restored,
    RestoreReason,
    agreed_requirement_ids,
    protect_requirements,
    requirement_key,
)

__all__ = [
    "ADOPT_LABEL",
    "AGREED_KINDS",
    "AGREEMENT_ORIGINS",
    "RESTORE_REASONS",
    "USERS_KEY",
    "USERS_LABEL",
    "AgreedItem",
    "AgreedKind",
    "AgreementOrigin",
    "RestoreReason",
    "Restored",
    "agreed_requirement_ids",
    "agreement_items",
    "body_of",
    "integration_key",
    "origin_label",
    "protect_requirements",
    "removed_keys",
    "requirement_key",
]
