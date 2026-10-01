"""詳細設計モード(ステージ4)のドメインロジック(純粋関数)。"""

from app.detailed_design.stages import (
    STAGE_INPUTS,
    STAGES,
    Fingerprint,
    StageInputs,
    StageRecord,
    StageState,
    StageView,
    StoredStatus,
    can_approve,
    current_inputs,
    derive_states,
    doc_key,
    stage_key,
)

__all__ = [
    "STAGES",
    "STAGE_INPUTS",
    "Fingerprint",
    "StageInputs",
    "StageRecord",
    "StageState",
    "StageView",
    "StoredStatus",
    "can_approve",
    "current_inputs",
    "derive_states",
    "doc_key",
    "stage_key",
]
