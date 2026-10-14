"""差し戻し: 採用したヒアリングの指摘から、聞き直す観点と最初の問いかけを組み立てる(純粋関数)。

ヒアリングが原因の指摘(利用者に聞き直さないと決められない)は、再生成では直せない。観点を未決に
戻し、チャットで聞き直す。最初の問いかけはコードが組み立てる(LLM を呼ばない。指摘から外れず、
Gemini の障害でも出せるように)。以後の往復は今のヒアリング(未決の観点を聞く)がそのまま続ける。
"""

from collections.abc import Iterable

from app.hearing import POINT_DEFS, point_def
from app.review.sendback import SendbackFinding

REHEARING_HEAD = (
    "AI 設計レビューで、ヒアリングで決めきれていない点が見つかりました。"
    "次の観点について、もう一度確認させてください。"
)
REHEARING_TAIL = (
    "答えていただいた内容で、観点の表を更新します。決まったら、設計書を再生成してください。"
)


def can_rehear(finding: SendbackFinding) -> bool:
    """聞き直しに使える指摘か(採用した、ヒアリングが原因の、観点のキーのある指摘)。"""
    return (
        finding.decision == "adopted"
        and finding.cause == "hearing"
        and finding.point_key is not None
    )


def rehearing_points(findings: Iterable[SendbackFinding]) -> dict[str, list[SendbackFinding]]:
    """聞き直す観点 → その観点の指摘(観点の表の順)。"""
    by_key: dict[str, list[SendbackFinding]] = {}
    for f in findings:
        if can_rehear(f) and f.point_key is not None:
            by_key.setdefault(f.point_key, []).append(f)
    return {d.key: by_key[d.key] for d in POINT_DEFS if d.key in by_key}


def rehearing_message(points: dict[str, list[SendbackFinding]]) -> str:
    """チャットの最初の問いかけ(頭の1文 + 観点ごとに指摘と問いの例 + 締めの1文)。"""
    lines = [REHEARING_HEAD]
    for key, findings in points.items():
        definition = point_def(key)
        lines.append(f"・「{definition.label}」")
        for f in findings:
            lines += [f"  - {f.problem}", f"    改善案: {f.suggestion}"]
        lines.append(f"  例えば: {definition.question}")
    lines.append(REHEARING_TAIL)
    return "\n".join(lines)
