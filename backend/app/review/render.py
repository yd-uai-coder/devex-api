"""設計レビューの指摘をチャットに出す md にする(純粋関数)。"""

from collections.abc import Sequence

from app.review.schemas import CAUSE_LABEL, LEVEL_LABEL, REVIEW_LEVELS, ReviewFindingOut


def sort_findings(findings: Sequence[ReviewFindingOut]) -> list[ReviewFindingOut]:
    """重要度の順(最重要 → 中程度 → 軽微)。同じ重要度の中はモデルが返した順のまま。"""
    return sorted(findings, key=lambda f: REVIEW_LEVELS.index(f.level))


def render_findings(findings: Sequence[ReviewFindingOut]) -> str:
    """見出し・件数と、指摘ごとの重要度・原因・対象・問題・引用・改善案の md。"""
    counts = "・".join(
        f"{LEVEL_LABEL[level]} {n}"
        for level in REVIEW_LEVELS
        if (n := sum(1 for f in findings if f.level == level))
    )
    if not findings:
        return "## 設計レビュー\n指摘はありません。"
    lines = [f"## 設計レビュー\n指摘 {len(findings)}件({counts})", ""]
    for n, f in enumerate(sort_findings(findings), 1):
        point = f"・観点 {f.point_key}" if f.point_key else ""
        lines += [
            f"{n}. **[{LEVEL_LABEL[f.level]} / {CAUSE_LABEL[f.cause]}{point}]** "
            f"{', '.join(f.targets)}",
            f"   - 問題: {f.problem}",
            f"   - 引用: {f.evidence}",
            f"   - 理由: {f.reason}",
            f"   - 改善案: {f.suggestion}",
        ]
    return "\n".join(lines)
