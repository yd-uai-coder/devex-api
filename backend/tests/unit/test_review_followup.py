"""採用した指摘の追跡(app/review/followup.py)のテスト。

SUT は followup_state・version_keys・matches・document_model_rows・element_rows、ドライバは pytest。
スタブ不要 ── 対象が純粋(版・行の値を受け取って状態を返すだけ)で外部依存を呼ばないため。
"""

from app.detailed_design.impact import Element
from app.documents.requirements.model import RequirementRow, RequirementsModel
from app.review import (
    Row,
    RowDiff,
    document_model_rows,
    element_rows,
    followup_state,
    version_keys,
)
from app.review.followup import matches

_REVIEWED = {"requirements": 1, "external_design": 1, "stage:5": 2, "stage:7": 1}


def _row(id_: str, body: str = "{}", label: str = "") -> Row:
    return Row(id=id_, label=label or id_, body=body)


def _state(documents, targets, current, diffs=None):
    return followup_state(
        documents=documents,
        targets=targets,
        reviewed=_REVIEWED,
        current=current,
        diffs=diffs or {},
    )


def test_version_keys_maps_documents_to_stages() -> None:
    assert version_keys(["external_design"], _REVIEWED) == ["external_design"]
    # 詳細設計書は段階1〜6 のうち回にあるもの、実装計画書(詳細)は段階7
    assert version_keys(["detailed_design", "detailed_plan"], _REVIEWED) == ["stage:5", "stage:7"]
    # 関連文書が無ければ回の版のすべて
    assert version_keys(None, _REVIEWED) == list(_REVIEWED)
    assert version_keys([], {"requirements": 1}) == ["requirements"]


def test_matches_by_id_or_name() -> None:
    assert matches("FR-001", _row("FR-001"))
    assert matches("FR-001・FR-002 の認可", _row("FR-002"))
    assert not matches("FR-0010", _row("FR-001"))
    assert matches("テーブル users", _row("users", label="テーブル users"))
    assert matches("POST /api/v1/projects", _row("POST /api/v1/projects"))
    assert not matches("", _row("x"))


def test_unchanged_versions_are_unaddressed() -> None:
    current = {**_REVIEWED, "requirements": 2}  # 関連しない文書だけ変わった
    assert _state(["external_design"], ["SCR-001"], current) == "unaddressed"


def test_changed_version_with_unchanged_target_row() -> None:
    current = {**_REVIEWED, "external_design": 2}
    diff = RowDiff(
        before=[_row("SCR-001", "a"), _row("SCR-002", "b")],
        after=[_row("SCR-001", "a"), _row("SCR-002", "c")],
    )
    assert (
        _state(["external_design"], ["SCR-001"], current, {"external_design": diff})
        == "target_unchanged"
    )
    assert _state(["external_design"], ["SCR-002"], current, {"external_design": diff}) == "changed"


def test_added_or_removed_target_row_is_changed() -> None:
    current = {**_REVIEWED, "external_design": 2}
    added = RowDiff(before=[], after=[_row("SCR-003")])
    diffs = {"external_design": added}
    assert _state(["external_design"], ["SCR-003"], current, diffs) == "changed"


def test_unknown_rows_or_unmatched_targets_are_changed() -> None:
    current = {**_REVIEWED, "external_design": 2}
    # 行を比べられない(レビューした版が残っていない・実装計画書)
    assert _state(["external_design"], ["SCR-001"], current) == "changed"
    # 対象を行に結べない
    diff = RowDiff(before=[_row("SCR-001")], after=[_row("SCR-001")])
    assert (
        _state(["external_design"], ["画面の全体"], current, {"external_design": diff})
        == "changed"
    )


def test_stage_versions_follow_detailed_documents() -> None:
    current = {**_REVIEWED, "stage:5": 3}
    diff = RowDiff(before=[_row("F-01", "a")], after=[_row("F-01", "a")])
    assert _state(["detailed_plan"], ["F-01"], current, {"stage:5": diff}) == "unaddressed"
    assert _state(["detailed_design"], ["F-01"], current, {"stage:5": diff}) == "target_unchanged"


def test_document_model_rows_and_element_rows() -> None:
    model = RequirementsModel(
        purpose="p",
        rows=[RequirementRow(id="FR-001", kind="FR", text="ログインする", source="user")],
    )
    rows = document_model_rows("requirements", model.model_dump(mode="json"))
    assert rows is not None and [(r.id, r.label) for r in rows] == [("FR-001", "要件 FR-001")]
    assert document_model_rows("implementation_plan", {"x": 1}) is None
    assert document_model_rows("requirements", None) is None
    assert document_model_rows("requirements", {"rows": "broken"}) is None

    elements = [
        Element("fn:F-01", "function", 1, "F-01 ログイン", body="a"),
        Element("table:users", "table", 3, "users", body="b"),
    ]
    assert element_rows(elements, 1) == [Row(id="F-01", label="F-01 ログイン", body="a")]
