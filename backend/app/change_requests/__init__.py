"""変更要求(CR)の状態・遷移・重なりの判定と、対象(要件 ID と変更の種類)の扱い・影響の予測
(純粋関数)。

docs/internal_design.md 3.3節「11. 合意と変更の一巡」。
"""

from app.change_requests.prediction import (
    PredictedItem,
    PredictedTarget,
    Prediction,
    baseline_models,
    predict,
    predict_from_snapshots,
    requirement_texts,
)
from app.change_requests.states import (
    ACTIVE_STATUSES,
    BACKGROUND_SOURCE_LABEL,
    BACKGROUND_SOURCES,
    COPYABLE_STATUSES,
    DECISION_ACTIONS,
    INITIAL_STATUS,
    STATUS_LABEL,
    STATUSES,
    TRANSITIONS,
    BackgroundSource,
    ChangeRequestAction,
    ChangeRequestStatus,
    Overlap,
    allowed_actions,
    next_status,
    overlaps,
)
from app.change_requests.targets import (
    TARGET_KIND_LABEL,
    TARGET_KINDS,
    Target,
    TargetKind,
    normalize_targets,
    target_ids,
    targets_from_json,
    targets_to_json,
)

__all__ = [
    "ACTIVE_STATUSES",
    "BACKGROUND_SOURCES",
    "BACKGROUND_SOURCE_LABEL",
    "COPYABLE_STATUSES",
    "DECISION_ACTIONS",
    "INITIAL_STATUS",
    "STATUSES",
    "STATUS_LABEL",
    "TARGET_KINDS",
    "TARGET_KIND_LABEL",
    "TRANSITIONS",
    "BackgroundSource",
    "ChangeRequestAction",
    "ChangeRequestStatus",
    "Overlap",
    "PredictedItem",
    "PredictedTarget",
    "Prediction",
    "Target",
    "TargetKind",
    "allowed_actions",
    "baseline_models",
    "next_status",
    "normalize_targets",
    "overlaps",
    "predict",
    "predict_from_snapshots",
    "requirement_texts",
    "target_ids",
    "targets_from_json",
    "targets_to_json",
]
