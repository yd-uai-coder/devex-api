"""変更の影響のたどり方(処理を軸)と理由・再生成の候補のテスト。

SUT: impact_of / regeneration_targets / EDGE_REASON(app/detailed_design/impact/trace.py)、
     パッケージの re-export(app/detailed_design/impact)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、DB や LLM を呼ばないため。要素は fixture の model から
`build_elements`で作る(導出そのものは test_impact_elements.py が確かめる)。

見本(appendix/change-impact-sample/)の例1〜3 と同じ形の変更を、予約の2処理の設計で確かめる。
"""

from tests.fixtures.change_impact import ROUTE, SERVICE, impact_documents, impact_stages

from app.detailed_design.impact import (
    EDGE_REASON,
    REGENERATION_STAGES,
    USER_REASON,
    Change,
    DesignModels,
    Element,
    ImpactItem,
    Ref,
    build_elements,
    impact_of,
    regeneration_targets,
)


def _elements(
    stages: dict[int, dict] | None = None, documents: dict[str, dict] | None = None
) -> list[Element]:
    models = DesignModels.from_dicts(stages or impact_stages(), documents or impact_documents())
    return build_elements(models)


def _keys(items: list[ImpactItem]) -> list[str]:
    return [item.key for item in items]


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_function_change_shows_own_elements_with_paths_and_reasons():
    elements = _elements()

    items = impact_of(elements, Change("fn:F-02"))

    assert _keys(items) == [
        "summary:F-02",
        "crud:F-02×reservations",
        f"module:{ROUTE}",
        "module:app/repositories/reservation.py",
        f"module:{SERVICE}",
        "proc:F-02",
        f"logic:{SERVICE}::list",
        "task:U-003",
        "unit:U-003",
    ]
    logic = items[6]
    assert logic.path == ("fn:F-02", "proc:F-02", f"logic:{SERVICE}::list")
    assert logic.reason == EDGE_REASON["called_by"] == "この手順から呼ばれる"
    assert [i.key for i in regeneration_targets(elements, items)] == [
        "proc:F-02",
        f"logic:{SERVICE}::list",
        "unit:U-003",
    ]
    assert sorted(REGENERATION_STAGES) == [5, 6, 8]


# --- 例1: 要件を変える ---


def test_requirement_change_reaches_function_and_its_elements():
    items = impact_of(_elements(), Change("req:FR-001"))
    by_key = {i.key: i for i in items}

    assert by_key["fn:F-01"].path == ("req:FR-001", "fn:F-01")
    assert by_key["fn:F-01"].reason == "この要件を持つ"
    assert by_key["unit:U-002"].path == ("req:FR-001", "fn:F-01", "proc:F-01", "unit:U-002")
    assert by_key["task:U-002"].reason == "この処理に属する"
    # 処理に属するモジュールは共有されるので示さない(直接の参照元でもない)
    assert not [k for k in by_key if k.startswith("module:")]


def test_shared_module_does_not_spread_to_other_function():
    """処理から先はモジュールを通らないので、文書の要素(画面)を共有しない別の処理には広がらない
    (F-02 の画面と GET の画面を外すと、F-01 と F-02 が共有するのはモジュールだけになる)。"""
    stages = impact_stages()
    stages[1]["functions"][1]["screens"] = []
    documents = impact_documents()
    documents["external_design"]["apis"][1]["screens"] = []
    items = impact_of(_elements(stages, documents), Change("req:FR-001"))

    assert "fn:F-02" not in _keys(items)
    assert "proc:F-02" not in _keys(items)
    assert "unit:U-003" not in _keys(items)


def test_shorter_path_wins_when_reached_twice():
    """F-01 は要件から直接と画面経由の2つの道で届くが、短い道を残す。"""
    items = impact_of(_elements(), Change("req:FR-001"))

    [f01] = [i for i in items if i.key == "fn:F-01"]
    assert len(f01.path) == 2


# --- 例2: モジュールの改名 ---


def test_rename_shows_only_direct_references():
    items = impact_of(_elements(), Change(f"module:{SERVICE}", "rename"))

    assert _keys(items) == [
        f"module:{ROUTE}",
        "proc:F-01",
        "proc:F-02",
        f"logic:{SERVICE}::list",
        f"logic:{SERVICE}::register",
        "cc:トランザクション",
        "task:U-002",
        "task:U-003",
        "unit:U-002",
        "unit:U-003",
    ]
    assert all(len(i.path) == 2 for i in items)
    # 段階4の中の依存(入口 → サービス)にも印が付く
    assert items[0].reason == "このモジュールに依存する"


def test_module_content_change_reaches_functions_that_call_it():
    items = impact_of(_elements(), Change(f"module:{SERVICE}"))
    by_key = {i.key: i for i in items}

    assert by_key["fn:F-01"].path == (f"module:{SERVICE}", "proc:F-01", "fn:F-01")
    assert by_key["fn:F-01"].reason == USER_REASON
    assert "summary:F-02" in by_key


# --- 例3: 非機能要件 → 横断事項 → 手順書 ---


def test_non_functional_requirement_reaches_units_through_crosscutting():
    items = impact_of(_elements(), Change("req:NFR-001"))

    assert [(i.key, i.path, i.reason) for i in items] == [
        ("cc:認証", ("req:NFR-001", "cc:認証"), "この要件を持つ"),
        (
            "unit:U-002",
            ("req:NFR-001", "cc:認証", "unit:U-002"),
            "この横断事項の対象のモジュールを含む",
        ),
        (
            "unit:U-003",
            ("req:NFR-001", "cc:認証", "unit:U-003"),
            "この横断事項の対象のモジュールを含む",
        ),
    ]


# --- その他 ---


def test_table_change_reaches_repository_crud_and_procedure_functions():
    items = impact_of(_elements(), Change("table:reservations"))
    by_key = {i.key: i for i in items}

    assert by_key["proc:F-01"].reason == "このテーブルを読み書きする"
    # テーブルを読み書きするリポジトリは直接の参照元として示す(段階5の手順の行から導いた辺)
    assert by_key["module:app/repositories/reservation.py"].path == (
        "table:reservations",
        "module:app/repositories/reservation.py",
    )
    assert by_key["fn:F-02"].path == ("table:reservations", "crud:F-02×reservations", "fn:F-02")
    assert "unit:U-003" in by_key


def test_unknown_or_unreferenced_change_shows_nothing():
    elements = _elements()

    assert impact_of(elements, Change("req:FR-404")) == []
    assert impact_of(elements, Change("unit:U-002")) == []


def test_items_sorted_by_stage_then_key():
    elements = [
        Element("b", "function", 1, "b", (Ref("x", "requirement"),)),
        Element("a", "function", 1, "a", (Ref("x", "requirement"),)),
        Element("s", "screen", 0, "s", (Ref("x", "requirement"),)),
        Element("x", "requirement", 0, "x"),
    ]

    assert _keys(impact_of(elements, Change("x"))) == ["s", "a", "b"]
