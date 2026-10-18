"""段階7 横断事項と実装計画の意味モデルと検証のテスト。

SUT: milestone_id / task_id / unit_ids / milestone_functions / planned_function_ids /
     unplanned_functions / is_file_path / missing_topics / normalize_plan /
     unit_key / assign_unit_keys / carry_over_unit_keys
     (app/detailed_design/plan.py)、
     validate_plan / STAGE_VALIDATORS(app/detailed_design/validation.py)、
     パッケージの re-export(app/detailed_design/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。段階1・4の内容は DB から
読まず、`StageSources`に dict で渡す(読み取りはサービス層の責務)。
"""

from tests.fixtures.detailed_design import function_list_model, module_list_model, plan_model

from app.detailed_design import (
    CROSSCUTTING_TOPICS,
    MAX_UNIT_FUNCTIONS,
    PLAN_STAGE,
    STAGE_VALIDATORS,
    UNIT_KINDS,
    CrossCuttingRow,
    Milestone,
    PlanModel,
    PlanTask,
    Risk,
    StageSources,
    assign_unit_keys,
    carry_over_unit_keys,
    has_errors,
    is_file_path,
    milestone_functions,
    milestone_id,
    missing_topics,
    normalize_plan,
    planned_function_ids,
    task_id,
    unit_ids,
    unit_key,
    unplanned_functions,
    validate_stage,
)
from app.detailed_design.validation import validate_plan
from app.documents.external_design.model import ExternalDesignModel

ROUTE = "app/api/routes/reservations.py"


def _sources(
    *,
    functions: list[str] | None = None,
    paths: list[str] | None = None,
    known_req_ids: list[str] | None = None,
) -> StageSources:
    """段階1(F-01、`functions`を渡すとその処理ID も)と段階4(ROUTE の1行、`paths`を渡すと
    そのパスの行も)。`known_req_ids`を渡すと、外部設計書の model に要件ID の一覧を写す。"""
    function_list = function_list_model()
    for function_id in functions or []:
        row = dict(function_list["functions"][0], id=function_id, trigger=f"GET /{function_id}")
        function_list["functions"].append(row)
    module_list = module_list_model()
    for path in paths or []:
        module_list["modules"].append(dict(module_list["modules"][0], path=path))
    models = (
        {"external_design": ExternalDesignModel(known_req_ids=known_req_ids).model_dump()}
        if known_req_ids is not None
        else {}
    )
    return StageSources(stages={1: function_list, 4: module_list}, document_models=models)


def _task(model: dict, index: int = 1) -> dict:
    """`plan_model()`の M-01 のタスク(既定は機能の単位 M-01-T02)。"""
    return model["milestones"][0]["tasks"][index]


def _codes(model: dict, sources: StageSources | None = None) -> list[str]:
    return [issue.code for issue in validate_plan(model, sources or _sources())]


# --- 統合スモーク(公開 API を素で1回呼ぶ) ---


def test_smoke_fixture_model_passes_stage7_validation():
    assert PLAN_STAGE == 7
    assert STAGE_VALIDATORS[PLAN_STAGE] is validate_plan
    issues = validate_stage(PLAN_STAGE, plan_model(), _sources())
    assert issues == []
    assert not has_errors(issues)


# --- 純粋関数 ---


def test_milestone_id_numbers_from_order():
    assert [milestone_id(i) for i in (0, 9, 99)] == ["M-01", "M-10", "M-100"]


def test_task_id_and_unit_ids_number_from_order():
    assert task_id(0, 0) == "M-01-T01"
    assert task_id(1, 9) == "M-02-T10"
    model = PlanModel(
        milestones=[
            Milestone(name="a", tasks=[PlanTask(), PlanTask()]),
            Milestone(name="b"),
            Milestone(name="c", tasks=[PlanTask()]),
        ]
    )
    assert unit_ids(model) == ["M-01-T01", "M-01-T02", "M-03-T01"]
    assert UNIT_KINDS == ("feature", "base")


def test_milestone_functions_and_planned_function_ids_come_from_tasks():
    model = PlanModel(
        milestones=[
            Milestone(
                name="a",
                tasks=[
                    PlanTask(function_ids=["F-02", " F-01 "]),
                    PlanTask(function_ids=["F-01", ""]),
                ],
            ),
            Milestone(name="b", tasks=[PlanTask(function_ids=["F-03"])]),
        ]
    )
    assert milestone_functions(model.milestones[0]) == ["F-02", "F-01"]
    assert planned_function_ids(model) == {"F-01", "F-02", "F-03"}
    assert unplanned_functions(model, ["F-01", "F-04", "F-03", "F-05"]) == ["F-04", "F-05"]


def test_is_file_path_needs_an_extension_in_the_last_segment():
    assert is_file_path("app/api/routes/reservations.py")
    assert is_file_path("Dockerfile.dev")
    assert not is_file_path("frontend")
    assert not is_file_path("app/services/")
    assert not is_file_path(".github")


def test_missing_topics_lists_default_topics_without_rows():
    model = PlanModel(crosscutting=[CrossCuttingRow(topic=" 認証 "), CrossCuttingRow(topic="監視")])
    assert missing_topics(model) == [t for t in CROSSCUTTING_TOPICS if t != "認証"]


def test_normalize_plan_trims_drops_empty_rows_and_resolves_modules():
    model = PlanModel(
        crosscutting=[
            CrossCuttingRow(topic=" 認証 ", policy=" JWT ", modules=["routes/reservations", ""]),
            CrossCuttingRow(topic=" ", policy=" "),
        ],
        milestones=[
            Milestone(
                name=" 予約 ",
                tasks=[
                    PlanTask(
                        kind="feature",
                        title=" API ",
                        function_ids=["F-01", " F-01 ", ""],
                        depends_on=[" M-01-T01 ", "M-01-T01", ""],
                        modules=[ROUTE, "routes/reservations"],
                        config_files=[" Dockerfile ", "Dockerfile"],
                    ),
                    PlanTask(title=" "),
                ],
            ),
            Milestone(name=" ", tasks=[PlanTask(title=" ")]),
        ],
        environment=" Python ",
        risks=[Risk(risk=" 遅延 ", mitigation=" 削る "), Risk(risk=" ")],
    )
    result = normalize_plan(model, [ROUTE, "app/services/reservation.py"])
    assert result.crosscutting == [CrossCuttingRow(topic="認証", policy="JWT", modules=[ROUTE])]
    assert len(result.milestones) == 1
    milestone = result.milestones[0]
    assert milestone.name == "予約"
    assert milestone.tasks == [
        PlanTask(
            kind="feature",
            title="API",
            function_ids=["F-01"],
            depends_on=["M-01-T01"],
            modules=[ROUTE],
            config_files=["Dockerfile"],
        )
    ]
    assert result.environment == "Python"
    assert result.risks == [Risk(risk="遅延", mitigation="削る")]


# --- 検証 ---


def test_validate_rejects_invalid_shape():
    assert _codes({"milestones": [{"name": "a", "priority": "Won't"}]}) == ["INVALID_MODEL"]
    codes = _codes({"milestones": [{"name": "a", "tasks": [{"kind": "テスト"}]}]})
    assert codes == ["INVALID_MODEL"]


def test_validate_requires_a_milestone():
    model = plan_model()
    model["milestones"] = []
    codes = _codes(model)
    assert "NO_MILESTONE" in codes
    assert "UNPLANNED_FUNCTION" in codes


def test_validate_rejects_unknown_function_with_unit_target():
    issues = validate_plan(plan_model(function_ids=["F-99"]), _sources())
    errors = [(i.code, i.target) for i in issues if i.severity == "error"]
    assert errors == [("UNKNOWN_FUNCTION", "M-01-T02")]
    assert "UNPLANNED_FUNCTION" in [i.code for i in issues]


def test_validate_rejects_unknown_module_but_not_config_files_or_crosscutting_files():
    issues = validate_plan(plan_model(module="app/services/unknown.py"), _sources())
    assert [(i.code, i.target) for i in issues] == [("UNKNOWN_MODULE", "M-01-T02")]
    # 環境・設定のファイルと横断事項のファイルは例なので、モジュール一覧に無くても指摘しない
    model = plan_model()
    _task(model, 0)["config_files"] = ["docker-compose.yml", "app/unknown.py"]
    model["crosscutting"][0]["modules"] = ["Dockerfile"]
    assert _codes(model) == []


def test_validate_warns_directory_module():
    issues = validate_plan(plan_model(module="frontend"), _sources(paths=["frontend"]))
    assert [(i.code, i.severity, i.target) for i in issues] == [
        ("MODULE_NOT_FILE", "warning", "M-01-T02")
    ]


def test_validate_rejects_unknown_and_forward_dependencies():
    model = plan_model()
    _task(model, 0)["depends_on"] = ["M-01-T02"]  # 後ろの単位
    _task(model, 1)["depends_on"] = ["M-01-T02", "M-09-T01"]  # 自分と、一覧に無い単位
    issues = validate_plan(model, _sources())
    assert [(i.code, i.target) for i in issues] == [
        ("FORWARD_DEPENDENCY", "M-01-T01"),
        ("FORWARD_DEPENDENCY", "M-01-T02"),
        ("UNKNOWN_DEPENDENCY", "M-01-T02"),
    ]


def test_validate_allows_dependency_on_a_unit_of_an_earlier_milestone():
    model = plan_model()
    second = {"name": "予約の一覧", "goal": "", "priority": "Should", "tasks": [dict(_task(model))]}
    second["tasks"][0] = dict(second["tasks"][0], function_ids=["F-02"], depends_on=["M-01-T02"])
    model["milestones"].append(second)
    assert _codes(model, _sources(functions=["F-02"])) == []


def test_validate_warns_kind_mismatch_many_functions_and_no_modules():
    model = plan_model()
    _task(model, 0)["function_ids"] = ["F-01"]  # 基盤なのに処理がある
    extra = ["F-02", "F-03", "F-04"]
    _task(model, 1)["function_ids"] = ["F-01", *extra]
    _task(model, 1)["modules"] = []
    feature_without_functions = dict(_task(model), function_ids=[], depends_on=[])
    model["milestones"][0]["tasks"].append(feature_without_functions)
    issues = validate_plan(model, _sources(functions=extra))
    assert not has_errors(issues)
    assert len(_task(model, 1)["function_ids"]) > MAX_UNIT_FUNCTIONS
    assert [(i.code, i.target) for i in issues] == [
        ("KIND_MISMATCH", "M-01-T01"),
        ("MANY_FUNCTIONS", "M-01-T02"),
        ("NO_MODULES", "M-01-T02"),
        ("KIND_MISMATCH", "M-01-T03"),
        ("NO_MODULES", "M-01-T03"),
        ("DUPLICATE_FUNCTION", "F-01"),
    ]


def test_validate_rejects_empty_and_duplicate_milestone_names_with_milestone_target():
    model = plan_model()
    first = model["milestones"][0]
    model["milestones"] = [first, dict(first, tasks=[]), dict(first, name=" ", tasks=[])]
    issues = validate_plan(model, _sources())
    assert [(i.code, i.target) for i in issues if i.severity == "error"] == [
        ("DUPLICATE_MILESTONE", "M-01"),
        ("DUPLICATE_MILESTONE", "M-02"),
        ("EMPTY_MILESTONE_NAME", "M-03"),
    ]


def test_validate_rejects_empty_task_and_topic():
    model = plan_model()
    model["milestones"][0]["tasks"].append({"kind": "base", "title": " "})
    model["crosscutting"].append({"topic": " ", "policy": "x"})
    issues = validate_plan(model, _sources())
    assert ("EMPTY_TASK", "M-01-T03") in [(i.code, i.target) for i in issues]
    assert "EMPTY_TOPIC" in [i.code for i in issues]


def test_validate_warns_unplanned_function_with_function_target():
    issues = validate_plan(plan_model(), _sources(functions=["F-02"]))
    assert [(i.code, i.severity, i.target) for i in issues] == [
        ("UNPLANNED_FUNCTION", "warning", "F-02")
    ]


def test_validate_warns_missing_topic_empty_policy_tasks_and_risks():
    model = plan_model()
    model["crosscutting"] = [{"topic": "認証", "policy": " "}]
    model["milestones"][0]["tasks"] = []
    model["risks"] = []
    issues = validate_plan(model, _sources())
    assert not has_errors(issues)
    codes = [i.code for i in issues]
    assert codes.count("MISSING_TOPIC") == 3
    assert {"EMPTY_POLICY", "EMPTY_TASKS", "NO_RISKS"} <= set(codes)


def test_validate_warns_goal_without_task_with_milestone_target():
    model = plan_model()
    model["milestones"][0]["goal"] = "予約を登録でき、CI/CD が動く"
    issues = validate_plan(model, _sources())
    assert [(i.code, i.severity, i.target) for i in issues] == [
        ("GOAL_WITHOUT_TASK", "warning", "M-01")
    ]
    assert "「CI/CD」" in issues[0].message
    _task(model)["config_files"] = [".github/workflows/ci.yml"]
    assert validate_plan(model, _sources()) == []


# --- 作業単位の安定キー ---


def _keys(model: PlanModel) -> list[str]:
    return [t.key for m in model.milestones for t in m.tasks]


def test_assign_unit_keys_numbers_tasks_without_key_in_order():
    model = PlanModel(
        milestones=[
            Milestone(name="a", tasks=[PlanTask(title="x"), PlanTask(title="y")]),
            Milestone(name="b", tasks=[PlanTask(title="z")]),
        ]
    )
    keyed = assign_unit_keys(model)
    assert unit_key(7) == "U-007"
    assert _keys(keyed) == ["U-001", "U-002", "U-003"]
    assert keyed.next_unit_seq == 4


def test_assign_unit_keys_keeps_existing_and_renumbers_duplicates():
    model = PlanModel(
        milestones=[
            Milestone(
                name="a",
                tasks=[
                    PlanTask(key="U-005", title="x"),
                    PlanTask(key="U-005", title="y"),
                    PlanTask(title="z"),
                ],
            )
        ],
        # 古い番号でも、今あるキーより後ろから振る
        next_unit_seq=2,
    )
    keyed = assign_unit_keys(model)
    assert _keys(keyed) == ["U-005", "U-006", "U-007"]
    assert keyed.next_unit_seq == 8


def test_keys_stay_with_tasks_when_reordered():
    keyed = assign_unit_keys(PlanModel.model_validate(plan_model()))
    tasks = keyed.milestones[0].tasks
    reordered = keyed.model_copy(
        update={"milestones": [keyed.milestones[0].model_copy(update={"tasks": tasks[::-1]})]}
    )
    again = assign_unit_keys(reordered)
    assert [(t.key, t.title) for t in again.milestones[0].tasks] == [
        ("U-002", "予約を登録する"),
        ("U-001", "開発環境を用意する"),
    ]
    assert again.next_unit_seq == 3


def test_carry_over_unit_keys_by_unique_title():
    previous = PlanModel(
        milestones=[
            Milestone(
                name="a",
                tasks=[
                    PlanTask(key="U-001", title="準備"),
                    PlanTask(key="U-002", title="登録"),
                    PlanTask(key="U-003", title="同名"),
                    PlanTask(key="U-004", title="同名"),
                ],
            )
        ],
        next_unit_seq=5,
    )
    regenerated = PlanModel(
        milestones=[
            Milestone(name="b", tasks=[PlanTask(title="登録"), PlanTask(title="同名")]),
            Milestone(name="c", tasks=[PlanTask(title="新しい"), PlanTask(title=" 準備 ")]),
        ]
    )
    carried = carry_over_unit_keys(regenerated, previous)
    # 同じ名前が前に2つある「同名」は引き継がず、前の番号の続きから振る
    assert _keys(carried) == ["U-002", "U-005", "U-006", "U-001"]
    assert carried.next_unit_seq == 7


def test_normalize_plan_keeps_keys_and_next_seq():
    model = PlanModel(
        milestones=[Milestone(name="a", tasks=[PlanTask(key=" U-003 ", title="x")])],
        next_unit_seq=4,
    )
    normalized = normalize_plan(model, [])
    assert _keys(normalized) == ["U-003"]
    assert normalized.next_unit_seq == 4


# --- 横断事項の要件ID ---


def test_normalize_plan_keeps_crosscutting_requirement_ids():
    model = PlanModel(
        crosscutting=[CrossCuttingRow(topic="認証", policy="JWT", req_ids=[" NFR-001 ", "NFR-001"])]
    )
    assert normalize_plan(model, []).crosscutting[0].req_ids == ["NFR-001"]


def test_validate_warns_unknown_crosscutting_requirement():
    model = plan_model()
    model["crosscutting"][1]["req_ids"] = ["NFR-001", "NFR-009"]

    issues = validate_plan(model, _sources(known_req_ids=["FR-001", "NFR-001"]))
    # 要件ID の一覧の無いプロジェクト(外部設計書の model が無い)は照合しない
    unchecked = validate_plan(model, _sources())

    assert [(i.code, i.severity, i.target) for i in issues] == [
        ("UNKNOWN_REQUIREMENT_ID", "warning", "認証")
    ]
    assert unchecked == []


def test_carry_over_unit_keys_by_overlap_and_similar_title():
    """タスク名が言い換えられても、処理・要件の重なりが大きい順に引き継ぐ。重なりの無い基盤の
    タスクはタスク名が似ていれば引き継ぐ。すでに前のキーを持つタスクはそのキーのまま。"""
    previous = PlanModel(
        milestones=[
            Milestone(
                name="a",
                tasks=[
                    PlanTask(key="U-001", kind="base", title="プロジェクト初期化と環境構築"),
                    PlanTask(key="U-002", title="登録", function_ids=["F-01"], req_ids=["FR-001"]),
                    PlanTask(key="U-003", title="一覧", function_ids=["F-02"], req_ids=["FR-002"]),
                    PlanTask(key="U-004", title="削除", function_ids=["F-03"]),
                ],
            )
        ],
        next_unit_seq=5,
    )
    regenerated = PlanModel(
        milestones=[
            Milestone(
                name="b",
                tasks=[
                    PlanTask(kind="base", title="プロジェクト初期構成と環境構築"),
                    PlanTask(title="一覧の表示", function_ids=["F-02"], req_ids=["FR-002"]),
                    PlanTask(
                        title="登録と確認", function_ids=["F-01", "F-04"], req_ids=["FR-001"]
                    ),
                    PlanTask(key="U-004", title="取り消し", function_ids=["F-09"]),
                    PlanTask(title="通知", function_ids=["F-05"]),
                ],
            )
        ]
    )
    carried = carry_over_unit_keys(regenerated, previous)

    assert _keys(carried) == ["U-001", "U-003", "U-002", "U-004", "U-005"]
    assert carried.next_unit_seq == 6
