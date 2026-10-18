"""設計レビューの指摘をチャットに出す文にする(純粋関数)。

チャットには件数の一行の知らせ(`render_notice`)を出し、指摘はレビューのパネルで見る。
`render_findings`(指摘の一覧の md)は、パネルの無い所(ログ・測定)で読むために残す。"""

from collections.abc import Sequence

from app.review.schemas import CAUSE_LABEL, LEVEL_LABEL, REVIEW_LEVELS, ReviewFindingOut


def sort_findings(findings: Sequence[ReviewFindingOut]) -> list[ReviewFindingOut]:
    """重要度の順(最重要 → 中程度 → 軽微)。同じ重要度の中はモデルが返した順のまま。"""
    return sorted(findings, key=lambda f: REVIEW_LEVELS.index(f.level))


def _level_counts(findings: Sequence[ReviewFindingOut]) -> str:
    return "・".join(
        f"{LEVEL_LABEL[level]} {n}"
        for level in REVIEW_LEVELS
        if (n := sum(1 for f in findings if f.level == level))
    )


def render_notice(findings: Sequence[ReviewFindingOut], gate_label: str) -> str:
    """チャットに出す一行の知らせ(区切り・件数・重要度ごとの件数)。"""
    if not findings:
        return f"設計レビュー({gate_label})をしました。指摘はありません。"
    return (
        f"設計レビュー({gate_label})をしました。指摘 {len(findings)}件"
        f"({_level_counts(findings)})。レビューのパネルで確かめてください。"
    )


def render_findings(findings: Sequence[ReviewFindingOut]) -> str:
    """見出し・件数と、指摘ごとの重要度・原因・対象・問題・引用・改善案の md。"""
    counts = _level_counts(findings)
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
