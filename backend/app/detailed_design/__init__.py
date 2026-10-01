"""詳細設計モード(ステージ4)のドメインロジック(純粋関数)。"""

from app.detailed_design.api_list import (
    ApiEndpoint,
    endpoint_key,
    extract_api_endpoints,
    parse_trigger,
    trigger_key,
)
from app.detailed_design.function_list import (
    FunctionDraft,
    FunctionListModel,
    FunctionRow,
    initial_group,
    merge_draft,
)
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
from app.detailed_design.validation import (
    STAGE_VALIDATORS,
    StageIssue,
    StageSources,
    has_errors,
    validate_stage,
)

__all__ = [
    "STAGES",
    "STAGE_INPUTS",
    "STAGE_VALIDATORS",
    "ApiEndpoint",
    "Fingerprint",
    "FunctionDraft",
    "FunctionListModel",
    "FunctionRow",
    "StageInputs",
    "StageIssue",
    "StageRecord",
    "StageSources",
    "StageState",
    "StageView",
    "StoredStatus",
    "can_approve",
    "current_inputs",
    "derive_states",
    "doc_key",
    "endpoint_key",
    "extract_api_endpoints",
    "has_errors",
    "initial_group",
    "merge_draft",
    "parse_trigger",
    "stage_key",
    "trigger_key",
    "validate_stage",
]
