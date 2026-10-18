"""設計レビューの区切り(どの時点で、どの文書をレビューするか)と区切りの状態(純粋関数)。

区切りは3つ。documents = 文書の生成の後(簡易は4文書、詳細は要件・外部。自動)、stage6 = 段階6の
承認の後(要件・外部 + 詳細設計書。ボタン)、stage7 = 段階7の承認の後(+ 実装計画書。ボタン)。
どの区切りも上流の文書を一緒に渡す(上流の欠陥から落ちないように)。

回はレビューした版(文書の版・段階の承認した版)を`targets`に持ち、今の版と一致すればその区切りは
レビュー済み、違えば未レビュー(文書を作り直した・段階を承認し直した後で、まだレビューしていない)。
"""

from collections.abc import Mapping, Sequence
from typing import Literal

from app.detailed_design.stages import stage_key
from app.detailed_design.validation import Severity, StageIssue
from app.documents.issues import DocumentIssue, IssueLevel

ReviewGate = Literal["documents", "stage6", "stage7"]
GateStatus = Literal["not_ready", "unreviewed", "running", "reviewed", "failed"]

DOCUMENTS_GATE: ReviewGate = "documents"

# 区切りがそろうのに要る、承認済みの段階
GATE_STAGES: dict[ReviewGate, tuple[int, ...]] = {
    "documents": (),
    "stage6": (1, 2, 3, 4, 5, 6),
    "stage7": (1, 2, 3, 4, 5, 6, 7),
}

GATE_LABEL: dict[ReviewGate, str] = {
    "documents": "文書の生成の後",
    "stage6": "段階6の承認の後",
    "stage7": "段階7の承認の後",
}

# 段階の区切りに渡す、詳細設計の文書の種類(本文は詳細設計書の組み立てから作る)
DETAILED_DESIGN_DOC = "detailed_design"
DETAILED_PLAN_DOC = "detailed_plan"

# 段階の検証の重要度を、文書の検証の重要度へ読み替える(既知の指摘として渡すため)
_SEVERITY_LEVEL: dict[Severity, IssueLevel] = {"error": "major", "warning": "minor"}


def gates_for_mode(mode: str) -> tuple[ReviewGate, ...]:
    """モードの区切り(簡易は文書だけ、詳細は文書・段階6・段階7)。"""
    return ("documents", "stage6", "stage7") if mode == "detailed" else ("documents",)


def gate_status(*, ready: bool, run_status: str | None, is_current: bool) -> GateStatus:
    """区切りの状態。実行中の回があれば実行中。そろっていなければ「まだ無い」。回が無い・
    レビューした版が今と違えば未レビュー。最新の回の結果が失敗なら失敗、成功ならレビュー済み。"""
    if run_status == "running":
        return "running"
    if not ready:
        return "not_ready"
    if run_status is None or not is_current:
        return "unreviewed"
    return "failed" if run_status == "failed" else "reviewed"


def stage_targets(
    doc_versions: Mapping[str, int],
    approved_versions: Mapping[int, int | None],
    stages: Sequence[int],
) -> dict[str, int] | None:
    """段階の区切りでレビューする版 {doc_type: 版, "stage:N": 承認した版}。段階が1つでも
    承認済みでなければ None(区切りがそろっていない)。"""
    targets = dict(doc_versions)
    for stage in stages:
        version = approved_versions.get(stage)
        if version is None:
            return None
        targets[stage_key(stage)] = version
    return targets


def stage_known_issues(
    issues: Mapping[int, Sequence[StageIssue]],
) -> list[tuple[str, DocumentIssue]]:
    """段階の検証の指摘を、既知の指摘(文書の種類 = "stage:N", 指摘)の組にする(段階の順)。"""
    return [
        (
            stage_key(stage),
            DocumentIssue(
                level=_SEVERITY_LEVEL[issue.severity],
                code=issue.code,
                message=issue.message,
                targets=(issue.target,) if issue.target else (),
            ),
        )
        for stage in sorted(issues)
        for issue in issues[stage]
    ]
