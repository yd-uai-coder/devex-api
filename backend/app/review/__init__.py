"""設計レビュー(生成とは別のモデルが指摘だけを返す)の入力・出力・判定(純粋関数)。

docs/internal_design.md 3.3節「8. AI 設計レビュー + テスト設計」。
"""

from app.review.known_issues import known_issues
from app.review.policy import review_effective
from app.review.prompt import (
    REVIEW_SYSTEM_PROMPT,
    build_points_table,
    build_review_messages,
    format_known_issues,
)
from app.review.render import render_findings, sort_findings
from app.review.schemas import (
    CAUSE_LABEL,
    LEVEL_LABEL,
    REVIEW_LEVELS,
    ReviewCause,
    ReviewFindingOut,
    ReviewLevel,
    ReviewOutput,
)

__all__ = [
    "CAUSE_LABEL",
    "LEVEL_LABEL",
    "REVIEW_LEVELS",
    "REVIEW_SYSTEM_PROMPT",
    "ReviewCause",
    "ReviewFindingOut",
    "ReviewLevel",
    "ReviewOutput",
    "build_points_table",
    "build_review_messages",
    "format_known_issues",
    "known_issues",
    "render_findings",
    "review_effective",
    "sort_findings",
]
