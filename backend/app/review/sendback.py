"""差し戻し: 採用した設計・不整合の指摘から、再生成の指示の文を組み立てる(純粋関数)。

直すのは生成のモデル(レビューは設計を書き換えない)。指示は、文書・段階の生成のプロンプトの
【修正指示】の節に入れる。指摘に関係しない行は変えない(前の版の差分にとどめる。
app/documents/revision.py)ことを、指示の頭に書く。ヒアリングが原因の指摘は、再生成では直せない
(利用者に聞き直さないと決められない)ので、聞き直し(app/review/rehearing.py)へ回す。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.review.schemas import LEVEL_LABEL

_LEVEL_TEXT: dict[str, str] = {str(k): v for k, v in LEVEL_LABEL.items()}

INSTRUCTION_HEAD = (
    "次の設計レビューの指摘を反映して書き直す。指摘の対象と、それに合わせて変える必要のある行だけを"
    "書き直し、指摘に関係しない行は変えない(unchanged にする)。"
)


@dataclass(frozen=True)
class SendbackFinding:
    """指示に使う指摘の項目(保存した指摘 app/models/review.py の ReviewFinding から写す)。"""

    level: str
    cause: str
    decision: str
    targets: Sequence[str]
    documents: Sequence[str] | None
    problem: str
    suggestion: str
    point_key: str | None = None
    evidence: str = ""

    @classmethod
    def of(cls, finding: Any) -> "SendbackFinding":
        """保存した指摘(同じ名前の属性を持つもの)から写す。"""
        return cls(
            level=finding.level,
            cause=finding.cause,
            decision=finding.decision,
            targets=tuple(finding.targets),
            documents=tuple(finding.documents) if finding.documents is not None else None,
            problem=finding.problem,
            suggestion=finding.suggestion,
            point_key=finding.point_key,
            evidence=finding.evidence,
        )


def can_regenerate(finding: SendbackFinding) -> bool:
    """再生成の指示に入れられる指摘か(採用した、ヒアリングが原因でない指摘)。"""
    return finding.decision == "adopted" and finding.cause != "hearing"


def findings_for(
    documents: Iterable[str], findings: Iterable[SendbackFinding]
) -> list[SendbackFinding]:
    """書き換える文書(`documents`)に関連する指摘。関連文書の無い指摘(古い指摘)はどれにも入れる。"""
    wanted = set(documents)
    return [f for f in findings if not f.documents or wanted & set(f.documents)]


def build_instruction(findings: Sequence[SendbackFinding]) -> str:
    """指示の文(頭の1文 + 指摘1件1行)。指摘が無ければ空。"""
    if not findings:
        return ""
    lines = [INSTRUCTION_HEAD]
    for i, f in enumerate(findings, start=1):
        level = _LEVEL_TEXT.get(f.level, f.level)
        targets = "、".join(f.targets) or "—"
        lines.append(
            f"{i}. [{level}] 対象: {targets} ── 問題: {f.problem} → 改善案: {f.suggestion}"
        )
    return "\n".join(lines)

