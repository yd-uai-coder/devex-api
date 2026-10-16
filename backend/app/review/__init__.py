"""設計レビュー(生成とは別のモデルが指摘だけを返す)の入力・出力・判定(純粋関数)。

docs/internal_design.md 3.3節「8. AI 設計レビュー + テスト設計」。
"""

from app.review.fix_check import (
    VERDICT_LABEL,
    FixCheckOut,
    FixVerdict,
    build_fix_check_messages,
    changed_rows_text,
    target_rows_text,
)
from app.review.followup import (
    FollowupState,
    Row,
    RowDiff,
    document_model_rows,
    element_rows,
    followup_state,
    version_keys,
)
from app.review.gates import (
    DETAILED_DESIGN_DOC,
    DETAILED_PLAN_DOC,
    DOCUMENTS_GATE,
    GATE_LABEL,
    GATE_STAGES,
    GateStatus,
    ReviewGate,
    gate_status,
    gates_for_mode,
    stage_known_issues,
    stage_targets,
)
from app.review.known_issues import known_issues
from app.review.policy import review_allowed, review_effective
from app.review.prompt import (
    REVIEW_SYSTEM_PROMPT,
    build_points_table,
    build_review_messages,
    format_known_issues,
)
from app.review.rehearing import (
    REHEARING_HEAD,
    can_rehear,
    rehearing_message,
    rehearing_points,
)
from app.review.render import render_findings, render_notice, sort_findings
from app.review.schemas import (
    CAUSE_LABEL,
    LEVEL_LABEL,
    REVIEW_DOCUMENTS,
    REVIEW_LEVELS,
    ReviewCause,
    ReviewFindingOut,
    ReviewLevel,
    ReviewOutput,
    only_reviewed_documents,
)
from app.review.sendback import (
    INSTRUCTION_HEAD,
    SendbackFinding,
    build_instruction,
    can_regenerate,
    findings_for,
)

__all__ = [
    "CAUSE_LABEL",
    "DETAILED_DESIGN_DOC",
    "DETAILED_PLAN_DOC",
    "DOCUMENTS_GATE",
    "FixCheckOut",
    "FixVerdict",
    "FollowupState",
    "GATE_LABEL",
    "GATE_STAGES",
    "GateStatus",
    "INSTRUCTION_HEAD",
    "ReviewGate",
    "LEVEL_LABEL",
    "REHEARING_HEAD",
    "REVIEW_DOCUMENTS",
    "REVIEW_LEVELS",
    "REVIEW_SYSTEM_PROMPT",
    "VERDICT_LABEL",
    "ReviewCause",
    "ReviewFindingOut",
    "ReviewLevel",
    "ReviewOutput",
    "Row",
    "RowDiff",
    "SendbackFinding",
    "build_fix_check_messages",
    "build_instruction",
    "build_points_table",
    "build_review_messages",
    "can_regenerate",
    "can_rehear",
    "changed_rows_text",
    "document_model_rows",
    "element_rows",
    "findings_for",
    "followup_state",
    "format_known_issues",
    "gate_status",
    "gates_for_mode",
    "known_issues",
    "only_reviewed_documents",
    "rehearing_message",
    "rehearing_points",
    "render_findings",
    "render_notice",
    "review_allowed",
    "review_effective",
    "sort_findings",
    "stage_known_issues",
    "stage_targets",
    "target_rows_text",
    "version_keys",
]
