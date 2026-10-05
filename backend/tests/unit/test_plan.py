"""段階7 横断事項と実装計画の意味モデルと検証のテスト。

SUT: milestone_id / planned_function_ids / unplanned_functions / missing_topics / normalize_plan
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
    PLAN_STAGE,
    STAGE_VALIDATORS,
    CrossCuttingRow,
    Milestone,
    PlanModel,
    PlanTask,
    Risk,
    StageSources,
    has_errors,
    milestone_id,
    missing_topics,
    normalize_plan,
    planned_function_ids,
    unplanned_functions,
    validate_stage,
)
from app.detailed_design.validation import validate_plan

ROUTE = "app/api/routes/reservations.py"


def _sources(*, functions: list[str] | None = None) -> StageSources:
    """段階1(F-01、`functions`を渡すとその処理ID も)と段階4(ROUTE の1行)。"""
    function_list = function_list_model()
    for function_id in functions or []:
        row = dict(function_list["functions"][0], id=function_id, trigger=f"GET /{function_id}")
        function_list["functions"].append(row)
    return StageSources(stages={1: function_list, 4: module_list_model()})


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


def test_planned_function_ids_collects_milestones_and_tasks():
    model = PlanModel(
        milestones=[
            Milestone(name="a", function_ids=["F-01"], tasks=[PlanTask(function_ids=[" F-02 "])]),
            Milestone(name="b", tasks=[PlanTask(function_ids=["F-03"])]),
        ]
    )
    assert planned_function_ids(model) == {"F-01", "F-02", "F-03"}
    assert unplanned_functions(model, ["F-01", "F-04", "F-03", "F-05"]) == ["F-04", "F-05"]


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
                function_ids=["F-01", " F-01 ", ""],
                tasks=[
                    PlanTask(title=" API ", modules=[ROUTE, ROUTE], function_ids=["F-01"]),
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
    assert milestone.function_ids == ["F-01"]
    assert milestone.tasks == [PlanTask(title="API", modules=[ROUTE], function_ids=["F-01"])]
    assert result.environment == "Python"
    assert result.risks == [Risk(risk="遅延", mitigation="削る")]


# --- 検証 ---


def test_validate_rejects_invalid_shape():
    assert _codes({"milestones": [{"name": "a", "priority": "Won't"}]}) == ["INVALID_MODEL"]


def test_validate_requires_a_milestone():
    model = plan_model()
    model["milestones"] = []
    codes = _codes(model)
    assert "NO_MILESTONE" in codes
    assert "UNPLANNED_FUNCTION" in codes


def test_validate_rejects_unknown_function():
    codes = _codes(plan_model(function_ids=["F-99"]))
    # マイルストーンとタスクの2か所
    assert codes.count("UNKNOWN_FUNCTION") == 2
    assert "UNPLANNED_FUNCTION" in codes


def test_validate_does_not_check_files():
    # ファイルの欄は例なので、モジュール一覧に無い環境のファイルも指摘しない
    assert _codes(plan_model(module="Dockerfile")) == []
    assert _codes(plan_model(module="app/services/unknown.py")) == []


def test_validate_rejects_empty_and_duplicate_milestone_names_with_milestone_target():
    model = plan_model()
    first = model["milestones"][0]
    model["milestones"] = [first, dict(first), dict(first, name=" ")]
    issues = validate_plan(model, _sources())
    assert [(i.code, i.target) for i in issues if i.severity == "error"] == [
        ("DUPLICATE_MILESTONE", "M-01"),
        ("DUPLICATE_MILESTONE", "M-02"),
        ("EMPTY_MILESTONE_NAME", "M-03"),
    ]


def test_validate_rejects_empty_task_and_topic():
    model = plan_model()
    model["milestones"][0]["tasks"].append({"area": "テスト", "title": " "})
    model["crosscutting"].append({"topic": " ", "policy": "x"})
    codes = _codes(model)
    assert "EMPTY_TASK" in codes
    assert "EMPTY_TOPIC" in codes


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
