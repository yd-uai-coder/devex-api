"""採用した指摘が直ったかの判定(狭い判定のモデル Claude Haiku)の入力と出力(純粋関数)。

対象と問いが決まった判定なので、文書の全体でなく、指摘(問題・改善案)と、対象の行のレビューした
ときの内容、今の対象の行とレビューの後に足した・変えた行だけを渡す(入力を小さくし、安いモデルでも
外れにくくする。直し方が「行を足す」ときに、対象の行だけでは直ったかが分からないため、足した行も
渡す)。対象を行に結べないときは、今の関連文書の本文を渡す。判定は記録して画面に小さく出すだけのシャドーモード
(精度を測ってから使い方を決める。docs/external_design.md 2.11節)。
"""

from collections.abc import Sequence
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.review.followup import Row, RowDiff, matches
from app.review.sendback import SendbackFinding

FixVerdict = Literal["fixed", "partial", "not_fixed", "unsure"]

VERDICT_LABEL: dict[FixVerdict, str] = {
    "fixed": "直った",
    "partial": "一部",
    "not_fixed": "いいえ",
    "unsure": "自信なし",
}
# 本文を渡すときの上限の文字数(Haiku の入力を抑える)
MAX_DOCUMENT_CHARS = 20000
NOT_FOUND_TEXT = "(対象の行が無い)"

FIX_CHECK_SYSTEM_PROMPT = (
    "あなたは設計書のレビューの指摘が、書き直した設計書で直ったかを判定する。\n"
    "【指摘】の問題が、【今の内容】で解消されたかだけを見る。指摘に無い問題は見ない。\n"
    "- fixed: 問題が解消された(改善案と違う直し方でも、問題が無くなっていればよい)\n"
    "- partial: 一部だけ解消された\n"
    "- not_fixed: 解消されていない\n"
    "- unsure: 渡された内容では判断できない\n"
    "reason には、判定の根拠を【今の内容】から1〜2文で書く。"
)


class FixCheckOut(BaseModel):
    """直ったかの判定(判定のモデルが返す形)。"""

    verdict: FixVerdict = Field(
        description="fixed=解消した / partial=一部だけ解消した / not_fixed=解消していない / "
        "unsure=判断できない"
    )
    reason: str = Field(description="判定の根拠(1〜2文)")


def target_rows_text(rows: Sequence[Row], targets: Sequence[str]) -> str:
    """指摘の対象の行(1行1つ「名前: 内容」)。無ければ「対象の行が無い」。"""
    lines = [f"{row.label}: {row.body}" for row in rows if any(matches(t, row) for t in targets)]
    return "\n".join(lines) or NOT_FOUND_TEXT


def changed_rows_text(diff: RowDiff, targets: Sequence[str]) -> str:
    """今の内容: 指摘の対象の行と、レビューの後に足した・変えた行(直し方が「行を足す」こともある
    ため、対象の行だけでは直ったかが分からない)。消えた行は名前だけを書く。"""
    before = {row.id: row for row in diff.before}
    after_ids = {row.id for row in diff.after}
    lines: list[str] = []
    for row in diff.after:
        old = before.get(row.id)
        if old is None:
            lines.append(f"{row.label}(新しい): {row.body}")
        elif old.body != row.body:
            lines.append(f"{row.label}(変わった): {row.body}")
        elif any(matches(t, row) for t in targets):
            lines.append(f"{row.label}: {row.body}")
    lines += [f"{row.label}(消えた)" for row in diff.before if row.id not in after_ids]
    return "\n".join(lines) or NOT_FOUND_TEXT


def build_fix_check_messages(
    finding: SendbackFinding, before: str, after: str
) -> list[BaseMessage]:
    """判定の入力。`before`・`after`は、レビューしたときと今の対象の行(または今の本文)。"""
    human = (
        f"## 指摘\n対象: {'、'.join(finding.targets) or '—'}\n"
        f"問題: {finding.problem}\n改善案: {finding.suggestion}\n\n"
        f"## レビューしたときの内容\n{before}\n\n"
        f"## 今の内容\n{after[:MAX_DOCUMENT_CHARS]}"
    )
    return [SystemMessage(content=FIX_CHECK_SYSTEM_PROMPT), HumanMessage(content=human)]
