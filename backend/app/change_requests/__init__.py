"""変更要求(CR)の状態・遷移・重なりの判定(純粋関数)。

docs/internal_design.md 3.3節「11. 合意と変更の一巡」。
"""

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
    normalize_targets,
    overlaps,
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
    "TRANSITIONS",
    "BackgroundSource",
    "ChangeRequestAction",
    "ChangeRequestStatus",
    "Overlap",
    "allowed_actions",
    "next_status",
    "normalize_targets",
    "overlaps",
]
