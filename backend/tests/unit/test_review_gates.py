"""設計レビューの区切り(app/review/gates.py)・チャットの知らせ・段階の区切りの入力のラベルのテスト。

SUT は区切りの純粋関数(`gates_for_mode`・`gate_status`・`stage_targets`・`stage_known_issues`)と
`render_notice`・`build_review_messages`、ドライバは pytest。スタブ不要 ── 対象が純粋(副作用なし)で
外部依存を呼ばないため。
"""

import pytest
from tests.fixtures.review import review_finding, review_output

from app.detailed_design.validation import StageIssue
from app.hearing import PointState
from app.review import (
    DETAILED_DESIGN_DOC,
    DETAILED_PLAN_DOC,
    GATE_STAGES,
    build_review_messages,
    gate_status,
    gates_for_mode,
    only_reviewed_documents,
    render_notice,
    stage_known_issues,
    stage_targets,
)


def test_gates_for_mode() -> None:
    assert gates_for_mode("simple") == ("documents",)
    assert gates_for_mode("detailed") == ("documents", "stage6", "stage7")


@pytest.mark.parametrize(
    ("ready", "run_status", "is_current", "expected"),
    [
        (False, None, False, "not_ready"),
        (False, "succeeded", False, "not_ready"),
        (False, "running", False, "running"),
        (True, "running", True, "running"),
        (True, None, False, "unreviewed"),
        (True, "succeeded", False, "unreviewed"),
        (True, "failed", False, "unreviewed"),
        (True, "succeeded", True, "reviewed"),
        (True, "failed", True, "failed"),
    ],
)
def test_gate_status(ready: bool, run_status: str | None, is_current: bool, expected: str) -> None:
    assert gate_status(ready=ready, run_status=run_status, is_current=is_current) == expected


def test_stage_targets_needs_every_stage_approved() -> None:
    documents = {"requirements": 2, "external_design": 1}
    approved = {1: 1, 2: 3, 3: 1, 4: 1, 5: 2, 6: 1}

    assert stage_targets(documents, approved, GATE_STAGES["stage6"]) == {
        "requirements": 2,
        "external_design": 1,
        "stage:1": 1,
        "stage:2": 3,
        "stage:3": 1,
        "stage:4": 1,
        "stage:5": 2,
        "stage:6": 1,
    }
    # 段階7が承認されていない
    assert stage_targets(documents, approved, GATE_STAGES["stage7"]) is None
    assert stage_targets(documents, {**approved, 7: None}, GATE_STAGES["stage7"]) is None
    # 呼び出し側の文書の版は書き換えない
    assert documents == {"requirements": 2, "external_design": 1}


def test_stage_known_issues_maps_severity_and_target() -> None:
    issues = {
        6: [StageIssue(severity="warning", code="W", message="弱い")],
        5: [StageIssue(severity="error", code="E", message="呼べない", target="F-01#2")],
    }

    known = stage_known_issues(issues)

    assert [(key, i.level, i.code, i.targets) for key, i in known] == [
        ("stage:5", "major", "E", ("F-01#2",)),
        ("stage:6", "minor", "W", ()),
    ]


def test_render_notice_counts_by_level() -> None:
    notice = render_notice(review_output().findings, "段階6の承認の後")

    assert notice == (
        "設計レビュー(段階6の承認の後)をしました。指摘 3件(最重要 1・中程度 1・軽微 1)。"
        "レビューのパネルで確かめてください。"
    )
    empty = render_notice([], "文書の生成の後")
    assert empty == "設計レビュー(文書の生成の後)をしました。指摘はありません。"


def test_stage_documents_and_issues_have_labels() -> None:
    documents = {
        "requirements": "# 要件",
        DETAILED_DESIGN_DOC: "# 詳細",
        DETAILED_PLAN_DOC: "# 計画",
    }
    issues = stage_known_issues({6: [StageIssue(severity="error", code="E", message="m")]})
    states = {"security": PointState(key="security", status="decided", summary="ログイン")}

    human = str(build_review_messages(documents, states, None, issues)[1].content)

    assert "## 要件定義書\n# 要件" in human
    assert "## 詳細設計書\n# 詳細" in human
    assert "## 実装計画書(詳細設計)\n# 計画" in human
    assert "- [段階6] 中程度 E: m" in human


def test_only_reviewed_documents_drops_documents_not_in_the_run() -> None:
    finding = review_finding(documents=("external_design", "internal_design", "detailed_design"))

    [limited] = only_reviewed_documents(
        [finding], ["requirements", "external_design", DETAILED_DESIGN_DOC]
    )

    assert limited.documents == ["external_design", "detailed_design"]
    assert finding.documents == ["external_design", "internal_design", "detailed_design"]
