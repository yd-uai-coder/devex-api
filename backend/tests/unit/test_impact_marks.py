"""見直しの印と「確認済み」のテスト。

SUT: marks_for / merged_elements / record / set_reviewed / blocking_marks / group_by_stage /
     StageImpact の保存形(to_dict・from_dict)(app/detailed_design/impact/marks.py)、
     パッケージの re-export(app/detailed_design/impact)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、DB や LLM を呼ばないため。前と後の要素は fixture の
model を書き換えて`build_elements`で作る(段階の行への記録はサービス層の責務)。
"""

import copy

from tests.fixtures.change_impact import SERVICE, impact_documents, impact_stages

from app.detailed_design.impact import (
    Element,
    Mark,
    StageImpact,
    blocking_marks,
    build_elements,
    diff_elements,
    doc_source,
    group_by_stage,
    marks_for,
    merged_elements,
    record,
    set_reviewed,
    stage_source,
)
from app.detailed_design.impact.elements import DesignModels

NEW_SERVICE = "app/services/reservation_service.py"


def _elements(stages: dict[int, dict], documents: dict[str, dict] | None = None) -> list[Element]:
    return build_elements(DesignModels.from_dicts(stages, documents or impact_documents()))


def _edit_function() -> tuple[list[Element], list[Element]]:
    """段階1の F-02 の名前を変えた前後。"""
    before = impact_stages()
    after = copy.deepcopy(before)
    after[1]["functions"][1]["name"] = "予約を検索する"
    return _elements(before), _elements(after)


def _marks_of_function_edit() -> tuple[list[Mark], list]:
    before, after = _edit_function()
    changes = diff_elements(before, after, stages=[1])
    return marks_for(before, after, changes, stage_source(1)), changes


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_function_edit_marks_own_elements_and_records_them_by_stage():
    marks, changes = _marks_of_function_edit()

    grouped = group_by_stage(marks)
    stage5 = record(StageImpact(), stage_source(1), changes, grouped[5])

    assert [m.key for m in grouped[5]] == ["proc:F-02"]
    # 段階4は処理を持つモジュール(直接の参照元)。モジュールの先へは広げない
    assert set(grouped) == {2, 3, 4, 5, 6, 7, 8}
    assert stage5.marks[0].path == ("fn:F-02", "proc:F-02")
    assert stage5.marks[0].reason == "この処理に属する"
    assert stage5.marks[0].sources == ("stage:1",)
    assert [c.change.key for c in stage5.changes] == ["fn:F-02"]
    assert (stage5.open_count, stage5.reviewed_count) == (1, 0)


# --- 印の求め方 ---


def test_document_elements_and_changed_elements_get_no_marks():
    """文書の要素(置き場 0)と、変えた要素そのものには印を付けない。"""
    documents = impact_documents()
    changed = copy.deepcopy(documents)
    changed["requirements"]["rows"][1]["text"] = "予約を絞り込んで見られる"
    before, after = _elements(impact_stages(), documents), _elements(impact_stages(), changed)
    changes = diff_elements(before, after, stages=[0])

    marks = marks_for(before, after, changes, doc_source("requirements"))

    assert all(m.stage >= 1 for m in marks)
    assert "fn:F-02" in {m.key for m in marks}
    assert "req:FR-002" not in {m.key for m in marks}


def test_rename_marks_referrers_still_pointing_to_the_old_key():
    """改名の前のキーは後の要素に無いが、まだ前のキーを参照している段階5・6 の要素に印が付く
    (前の要素の辺を合わせたグラフでたどる)。"""
    before_stages = impact_stages()
    after_stages = copy.deepcopy(before_stages)
    after_stages[4]["modules"][1]["path"] = NEW_SERVICE
    after_stages[4]["modules"][0]["depends_on"] = [NEW_SERVICE]
    before, after = _elements(before_stages), _elements(after_stages)
    changes = diff_elements(before, after, stages=[4])

    marks = marks_for(before, after, changes, stage_source(4))

    keys = {m.key for m in marks}
    assert {"proc:F-01", "proc:F-02"} <= keys
    assert f"logic:{SERVICE}::register" in keys
    # 改名は直接の参照元だけ。処理を軸に広げない(処理の要約・CRUD には届かない)
    assert not any(m.key.startswith(("summary:", "crud:")) for m in marks)
    assert all(len(m.path) == 2 for m in marks)
    # 依存する側のモジュール(入口)は、依存先の名前を書き換えただけなので直接の参照元として印が付く
    assert "module:app/api/routes/reservations.py" in keys


def test_merged_elements_keep_old_only_elements_and_old_refs():
    before = [Element("a", "module", 4, "a"), Element("b", "module", 4, "b")]
    after = [Element("b", "module", 4, "b")]

    merged = {e.key for e in merged_elements(before, after)}

    assert merged == {"a", "b"}


# --- 記録と確認済み ---


def test_new_change_reopens_reviewed_mark_and_adds_source():
    marks, changes = _marks_of_function_edit()
    stage5 = record(StageImpact(), stage_source(1), changes, group_by_stage(marks)[5])
    reviewed = set_reviewed(stage5, ["proc:F-02"], True)

    again = record(reviewed, doc_source("requirements"), changes, group_by_stage(marks)[5])

    assert reviewed.marks[0].reviewed is True
    assert again.marks[0].reviewed is False
    assert again.marks[0].sources == ("stage:1", "doc:requirements")
    assert {c.source for c in again.changes} == {"stage:1", "doc:requirements"}


def test_blocking_marks_ignore_own_and_reviewed_marks():
    own = Mark("module:a", "a", 4, ("module:b", "module:a"), "r", (stage_source(4),))
    other = Mark("module:c", "c", 4, ("fn:F-01", "module:c"), "r", (stage_source(1),))
    impact = StageImpact(marks=(own, other))

    assert blocking_marks(impact, 4) == [other]
    assert blocking_marks(set_reviewed(impact, ["module:c"], True), 4) == []
    # 自分の印でも、他から同じ要素に届いていれば止める
    both = Mark("module:a", "a", 4, ("module:b", "module:a"), "r", ("stage:4", "doc:requirements"))
    assert blocking_marks(StageImpact(marks=(both,)), 4) == [both]


def test_stage_impact_round_trips_and_drops_broken_items():
    marks, changes = _marks_of_function_edit()
    impact = record(StageImpact(), stage_source(1), changes, group_by_stage(marks)[5])

    stored = impact.to_dict()
    stored["marks"].append({"key": "x"})  # 形の正しくない印

    assert StageImpact.from_dict(stored) == impact
    assert StageImpact.from_dict(None) == StageImpact()
