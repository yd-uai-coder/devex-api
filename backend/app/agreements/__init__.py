"""合意の記録(ステージ12)の純粋関数。

docs/internal_design.md 3.3節「12. 合意の精度」。

- items: 合意の対象の要素(要件・利用者と使う場面・画面・API・外部連携)と比べる値
- origins: 合意の出どころと、画面に出す名前
- protection: 再生成の後に、合意済みの要素を合意の形へ戻す
- history: 要素1つの合意の履歴(合意の記録・戻した版・関わる CR)
- status: 表示中の版の要素ごとの合意の状態(合意済み・合意と違う・未合意・今の版に無い)
"""

from app.agreements.history import (
    HISTORY_EVENT_LABEL,
    HISTORY_EVENTS,
    RESTORE_REASON_LABEL,
    ChangeRequestRef,
    ElementHistory,
    HistoryEntry,
    HistoryEvent,
    RecordFacts,
    RelatedChangeRequest,
    VersionFacts,
    element_history,
)
from app.agreements.items import (
    AGREED_KINDS,
    USERS_KEY,
    USERS_LABEL,
    AgreedItem,
    AgreedKind,
    agreement_items,
    body_of,
    change_request_items,
    integration_key,
    kind_of,
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
    AgreedExternalIds,
    Restored,
    RestoreReason,
    agreed_external_ids,
    agreed_requirement_ids,
    protect_external_design,
    protect_requirements,
    requirement_key,
)
from app.agreements.status import (
    ELEMENT_STATE_LABEL,
    ELEMENT_STATES,
    CurrentAgreement,
    DocumentAgreementStatus,
    ElementState,
    ElementStatus,
    RestoredElement,
    element_states,
    restored_elements,
)

__all__ = [
    "ADOPT_LABEL",
    "AGREED_KINDS",
    "AGREEMENT_ORIGINS",
    "ELEMENT_STATES",
    "ELEMENT_STATE_LABEL",
    "HISTORY_EVENTS",
    "HISTORY_EVENT_LABEL",
    "RESTORE_REASONS",
    "RESTORE_REASON_LABEL",
    "USERS_KEY",
    "USERS_LABEL",
    "AgreedItem",
    "AgreedKind",
    "AgreementOrigin",
    "ChangeRequestRef",
    "CurrentAgreement",
    "DocumentAgreementStatus",
    "ElementState",
    "ElementStatus",
    "ElementHistory",
    "HistoryEntry",
    "HistoryEvent",
    "RecordFacts",
    "RelatedChangeRequest",
    "VersionFacts",
    "RestoreReason",
    "AgreedExternalIds",
    "Restored",
    "RestoredElement",
    "agreed_external_ids",
    "agreed_requirement_ids",
    "agreement_items",
    "body_of",
    "change_request_items",
    "element_history",
    "element_states",
    "integration_key",
    "kind_of",
    "origin_label",
    "protect_external_design",
    "protect_requirements",
    "removed_keys",
    "requirement_key",
    "restored_elements",
]
