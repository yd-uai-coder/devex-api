"""変更の影響を求めるための、設計の要素と参照の導出のテスト。

SUT: DesignModels.from_dicts / build_elements / parse_db_tables
     (app/detailed_design/impact/elements.py)、パッケージの re-export(app/detailed_design/impact)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、DB や LLM を呼ばないため。段階・文書の model は DB から
読まず、fixture の dict で渡す(読み取りはサービス層の責務)。
"""

from tests.fixtures.change_impact import (
    REPOSITORY,
    ROUTE,
    SERVICE,
    impact_documents,
    impact_stages,
)

from app.detailed_design.impact import (
    DOCUMENT_STAGE,
    DesignModels,
    Element,
    Ref,
    build_elements,
    parse_db_tables,
)


def _elements(**overrides) -> dict[str, Element]:
    stages = impact_stages() | overrides.pop("stages", {})
    models = DesignModels.from_dicts(stages, impact_documents(), **overrides)
    return {e.key: e for e in build_elements(models)}


def _refs(element: Element) -> set[tuple[str, str]]:
    return {(r.to, r.edge) for r in element.refs}


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_builds_elements_for_documents_and_all_stages():
    elements = _elements()

    stages = {e.stage for e in elements.values()}
    kinds = {e.kind for e in elements.values()}
    assert stages == {DOCUMENT_STAGE, 1, 2, 3, 4, 5, 6, 7, 8}
    assert kinds == {
        "requirement",
        "screen",
        "api",
        "function",
        "summary",
        "table",
        "crud",
        "module",
        "procedure",
        "logic",
        "task",
        "crosscutting",
        "unit",
    }
    assert elements["fn:F-01"] == Element(
        "fn:F-01",
        "function",
        1,
        "F-01 予約を登録する",
        (
            Ref("req:FR-001", "requirement"),
            Ref("screen:SCR-001", "screen"),
            Ref("api:POST /api/v1/reservations", "trigger"),
        ),
    )


# --- 種類ごとの辺 ---


def test_document_elements_reference_requirements_and_screens():
    elements = _elements()

    assert elements["req:NFR-001"].refs == ()
    assert _refs(elements["screen:SCR-001"]) == {
        ("req:FR-001", "requirement"),
        ("req:FR-002", "requirement"),
    }
    assert _refs(elements["api:GET /api/v1/reservations"]) == {
        ("screen:SCR-001", "screen"),
        ("req:FR-002", "requirement"),
    }


def test_stage_2_to_4_elements_reference_functions_tables_and_dependencies():
    elements = _elements()

    assert _refs(elements["summary:F-02"]) == {("fn:F-02", "function")}
    assert _refs(elements["crud:F-01×reservations"]) == {
        ("fn:F-01", "function"),
        ("table:reservations", "table"),
    }
    assert _refs(elements[f"module:{ROUTE}"]) == {
        ("fn:F-01", "function"),
        ("fn:F-02", "function"),
        (f"module:{SERVICE}", "depends"),
    }


def test_procedure_references_callees_and_tables_from_db_column():
    """手順は、呼び出し先のモジュールと、DB 欄のテーブル(大文字小文字はそろえる)を参照する。
    DB 欄と同じ行の呼び出し先のモジュールも、そのテーブルを参照する。"""
    elements = _elements()

    assert _refs(elements["proc:F-02"]) == {
        ("fn:F-02", "function"),
        (f"module:{ROUTE}", "calls"),
        (f"module:{SERVICE}", "calls"),
        (f"module:{REPOSITORY}", "calls"),
        ("table:reservations", "table"),
    }
    # DB 欄と同じ行の呼び出し先(リポジトリ)も、そのテーブルを読み書きする
    assert _refs(elements[f"module:{REPOSITORY}"]) == {
        ("fn:F-01", "function"),
        ("fn:F-02", "function"),
        ("table:reservations", "table"),
    }


def test_logic_is_placed_in_module_and_called_by_procedure():
    elements = _elements()

    assert _refs(elements[f"logic:{SERVICE}::register"]) == {
        (f"module:{SERVICE}", "placed"),
        ("proc:F-01", "called_by"),
    }


def test_plan_elements_use_stable_keys_and_crosscutting_requirements():
    elements = _elements()

    assert elements["task:U-002"].label == "M-01-T02 予約を登録する"
    assert ("fn:F-01", "function") in _refs(elements["task:U-002"])
    assert elements["task:U-001"].refs == ()
    assert _refs(elements["cc:認証"]) == {
        ("req:NFR-001", "requirement"),
        (f"module:{ROUTE}", "depends"),
    }


def test_unit_references_task_designs_and_crosscutting_it_covers():
    """手順書は、単位(キー)・単位の処理の手順と処理ロジックだけ(同じモジュールの別の処理の関数は
    含めない)・モジュール・対象のモジュールを含む横断事項を参照する。"""
    elements = _elements()

    assert _refs(elements["unit:U-003"]) == {
        ("task:U-003", "unit"),
        ("proc:F-02", "implements"),
        (f"logic:{SERVICE}::list", "implements"),
        (f"module:{ROUTE}", "depends"),
        (f"module:{SERVICE}", "depends"),
        (f"module:{REPOSITORY}", "depends"),
        ("cc:認証", "covers"),
        ("cc:トランザクション", "covers"),
    }


def test_units_follow_keys_after_reorder_and_skip_unknown_keys():
    stages = impact_stages()
    tasks = stages[7]["milestones"][0]["tasks"]
    tasks.reverse()
    stages[8]["units"].append({"unit_id": "M-09-T01", "unit_key": "U-009", "title": "消えた"})

    elements = _elements(stages=stages)

    assert elements["task:U-002"].label == "M-01-T02 予約を登録する"
    assert elements["unit:U-003"].label == "M-01-T01 の手順書"
    assert "unit:U-009" not in elements


def test_er_tables_and_missing_or_invalid_inputs():
    """ER のテーブルも要素にする。無い段階・形の正しくない段階・文書は空とみなす。"""
    elements = _elements(er_tables=("users",))
    empty = {e.key for e in build_elements(DesignModels.from_dicts({1: {"functions": "x"}}, None))}

    assert elements["table:users"].refs == ()
    assert empty == set()


def test_refs_to_unknown_elements_are_dropped():
    stages = impact_stages()
    stages[1]["functions"][0]["req_ids"] = ["FR-001", "FR-099"]

    elements = _elements(stages=stages)

    assert ("req:FR-099", "requirement") not in _refs(elements["fn:F-01"])


# --- DB 欄 ---


def test_parse_db_tables_keeps_known_names_in_order():
    known = ["reservations", "users"]

    assert parse_db_tables("users R, Reservations CU", known) == ["users", "reservations"]
    assert parse_db_tables("—", known) == []
    assert parse_db_tables("payments C(外部) users R users U", known) == ["users"]
