"""簡易ドキュメントモードの要素と参照・たどり方のテスト。

SUT: SimpleModels / build_simple_elements(app/detailed_design/impact/simple.py)、
     impact_of の簡易モードの軸(処理別データフロー・文書のタスク。trace.py)、
     パッケージの re-export(app/detailed_design/impact)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、DB や LLM を呼ばないため。差分と印は、前後の model から
`diff_elements`・`marks_for` を素で呼んで確かめる。

設計は tests/fixtures/change_impact.py の簡易モードの見本(DF-1 = POST・DF-2 = GET、
単位 U-001〜U-003)。
"""

import copy

from tests.fixtures.change_impact import (
    REPOSITORY,
    SIMPLE_REPOSITORIES,
    SIMPLE_ROUTES,
    SIMPLE_SERVICES,
    simple_impact_documents,
    simple_impact_stage8,
)

from app.detailed_design.impact import (
    DOCUMENT_STAGE,
    Change,
    Element,
    ImpactItem,
    Ref,
    SimpleModels,
    build_simple_elements,
    diff_elements,
    doc_source,
    impact_of,
    marks_for,
)


def _elements(documents: dict[str, dict] | None = None) -> list[Element]:
    models = SimpleModels.from_dicts(simple_impact_stage8(), documents or simple_impact_documents())
    return build_simple_elements(models)


def _keys(items: list[ImpactItem]) -> list[str]:
    return [item.key for item in items]


def _units(items: list[ImpactItem]) -> list[str]:
    return [item.key for item in items if item.key.startswith("unit:")]


def _by_key(elements: list[Element]) -> dict[str, Element]:
    return {e.key: e for e in elements}


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_dataflow_change_reaches_its_task_and_unit():
    items = impact_of(_elements(), Change("df:DF-1"))

    assert _keys(items) == ["task:U-002", "unit:U-002"]
    assert items[1].reason == "この設計を手順書で実装する"


# --- 要素と参照 ---


def test_elements_keys_stages_and_refs():
    by_key = _by_key(_elements())

    df = by_key["df:DF-1"]
    assert (df.kind, df.stage) == ("dataflow", DOCUMENT_STAGE)
    assert df.label == "DF-1 POST /api/v1/reservations"
    assert df.refs == (
        Ref("api:POST /api/v1/reservations", "trigger"),
        Ref("table:reservations", "table"),
        Ref("table:users", "table"),
    )
    task = by_key["task:U-002"]
    assert (task.kind, task.stage) == ("task", DOCUMENT_STAGE)
    assert task.label == "M-02-T01 予約を登録する"
    # モジュールのファイルは、モジュール一覧の層の行にそろえる
    assert Ref(f"module:{SIMPLE_SERVICES}", "depends") in task.refs
    assert Ref("req:FR-001", "requirement") in task.refs
    unit = by_key["unit:U-003"]
    assert (unit.kind, unit.stage, unit.label) == ("unit", 8, "M-02-T02 の手順書")
    assert unit.refs == (
        Ref("task:U-003", "unit"),
        Ref("df:DF-2", "implements"),
        Ref(f"module:{SIMPLE_ROUTES}", "depends"),
        Ref(f"module:{SIMPLE_REPOSITORIES}", "depends"),
    )
    # WBS の依存(先に終わっている必要がある)は辺にしない
    assert not any(r.to.startswith("task:") for r in by_key["task:U-003"].refs)


def test_unit_without_task_is_skipped_and_missing_documents_are_empty():
    stage8 = simple_impact_stage8()
    stage8["units"].append({"unit_id": "M-09-T01", "unit_key": "U-099"})

    elements = build_simple_elements(SimpleModels.from_dicts(stage8, simple_impact_documents()))
    empty = build_simple_elements(SimpleModels.from_dicts(None, None))

    assert "unit:U-099" not in _by_key(elements)
    assert empty == []


def test_unit_module_files_are_matched_to_layer_rows():
    stage8 = simple_impact_stage8()
    stage8["units"][0]["files"] = [{"path": REPOSITORY, "kind": "module"}]

    models = SimpleModels.from_dicts(stage8, simple_impact_documents())
    by_key = _by_key(build_simple_elements(models))

    assert by_key["unit:U-001"].refs == (
        Ref("task:U-001", "unit"),
        Ref(f"module:{SIMPLE_REPOSITORIES}", "depends"),
    )


# --- たどり方(処理別データフローとタスクを軸) ---


def test_task_change_reaches_only_its_unit_not_dependents():
    # S8: タスクの内容を変えても、依存する後ろの単位(U-003)へは広げない
    items = impact_of(_elements(), Change("task:U-002"))

    assert _keys(items) == ["unit:U-002"]
    assert items[0].reason == "この作業単位の手順書"


def test_requirement_reaches_unit_through_its_task():
    # N4: 要件はタスクが直接持つ(詳細設計モードの処理の要件に当たる)
    items = {item.key: item for item in impact_of(_elements(), Change("req:FR-001"))}

    assert items["unit:U-002"].path == ("req:FR-001", "task:U-002", "unit:U-002")
    assert items["task:U-002"].reason == "この要件を持つ"


def test_table_change_reaches_dataflows_using_it():
    assert _units(impact_of(_elements(), Change("table:users"))) == ["unit:U-002"]
    assert _units(impact_of(_elements(), Change("table:reservations"))) == [
        "unit:U-002",
        "unit:U-003",
    ]


def test_module_change_shows_direct_referrers_only():
    # サービスの層を持つのは U-002 だけ。共有のモジュールからは処理へ広げない
    items = impact_of(_elements(), Change(f"module:{SIMPLE_SERVICES}"))

    assert _keys(items) == ["task:U-002", "unit:U-002"]


def test_base_unit_is_not_reached_by_feature_changes():
    for key in ("req:FR-001", "df:DF-2", "table:reservations"):
        assert "unit:U-001" not in _units(impact_of(_elements(), Change(key)))


# --- 差分と印(前後の版の model) ---


def test_removed_dataflow_marks_unit_that_still_refers_to_it():
    # トリガーが変わると DF の ID が変わる(前の版の DF-1 は消え、DF-3 になる)
    after_docs = copy.deepcopy(simple_impact_documents())
    flow = after_docs["internal_design"]["dataflows"][0]
    flow["id"], flow["trigger"] = "DF-3", "PUT /api/v1/reservations"
    before, after = _elements(), _elements(after_docs)

    changes = diff_elements(before, after, stages=(DOCUMENT_STAGE,))
    marks = marks_for(before, after, changes, doc_source("internal_design"))

    assert {(c.key, c.change) for c in changes} == {("df:DF-1", "removed"), ("df:DF-3", "added")}
    assert [m.key for m in marks] == ["unit:U-002"]
    assert marks[0].sources == ("doc:internal_design",)


def test_renamed_task_marks_unit_of_previous_key():
    # タスク名が変わるとキーが変わる(タスク名で引き継ぐため)。前のキーの手順書に印
    after_docs = copy.deepcopy(simple_impact_documents())
    task = after_docs["implementation_plan"]["milestones"][1]["tasks"][1]
    task["key"], task["title"] = "U-004", "予約を一覧で見る"
    before, after = _elements(), _elements(after_docs)

    changes = diff_elements(before, after, stages=(DOCUMENT_STAGE,))
    marks = marks_for(before, after, changes, doc_source("implementation_plan"))

    assert ("task:U-003", "removed") in {(c.key, c.change) for c in changes}
    assert [m.key for m in marks] == ["unit:U-003"]


def test_unchanged_documents_have_no_changes():
    assert diff_elements(_elements(), _elements(), stages=(DOCUMENT_STAGE,)) == []
