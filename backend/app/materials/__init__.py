"""資料(議事録・メール・資料)と合意済みの内容の違いの取り出し(純粋関数)。

docs/internal_design.md 3.3節「14. 変更の取り込み」。
"""

from app.materials.differences import (
    DIFFERENCE_KIND_LABEL,
    DIFFERENCE_STATUS_LABEL,
    DIFFERENCE_SYSTEM_PROMPT,
    MIN_QUOTE_CHARS,
    REPLACED_STATUSES,
    AgreedListing,
    CheckedDifference,
    DifferenceItemOut,
    DifferenceKind,
    DifferenceListOut,
    DifferenceStatus,
    MaterialFacts,
    agreed_listing,
    build_difference_messages,
    check_difference,
    check_differences,
    fresh_differences,
    normalize_quote,
    quote_found,
    resolve_key,
)

__all__ = [
    "DIFFERENCE_KIND_LABEL",
    "DIFFERENCE_STATUS_LABEL",
    "DIFFERENCE_SYSTEM_PROMPT",
    "MIN_QUOTE_CHARS",
    "REPLACED_STATUSES",
    "AgreedListing",
    "CheckedDifference",
    "DifferenceItemOut",
    "DifferenceKind",
    "DifferenceListOut",
    "DifferenceStatus",
    "MaterialFacts",
    "agreed_listing",
    "build_difference_messages",
    "check_difference",
    "check_differences",
    "fresh_differences",
    "normalize_quote",
    "quote_found",
    "resolve_key",
]
