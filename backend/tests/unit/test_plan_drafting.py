"""段階7の下書きの入出力(プロンプト・出力スキーマ・意味モデルへの変換)のテスト。

SUT: build_crosscutting_messages / build_plan_messages / to_crosscutting / to_plan_model
     (app/detailed_design/plan_drafting.py)、E2E 用の固定の出力(app/ai/llm/fake.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、LLM を呼ばないため(メッセージを組み立て、
構造化出力を受け取って変換するだけ。LLM の呼び出しはサービス層の責務)。
"""

from tests.fixtures.detailed_design import function_list_model, module_list_model

from app.ai.llm.fake import _UML_OUTPUTS
from app.detailed_design import (
    CROSSCUTTING_TOPICS,
    CrossCuttingRow,
    FunctionListModel,
    ModuleListModel,
    StageSources,
    normalize_plan,
    validate_stage,
)
from app.detailed_design.plan_drafting import (
    CROSSCUTTING_SYSTEM_PROMPT,
    PLAN_SYSTEM_PROMPT,
    CrossCuttingGenerationOutput,
    GeneratedCrossCutting,
    GeneratedMilestone,
    GeneratedRisk,
    GeneratedTask,
    PlanGenerationOutput,
    build_crosscutting_messages,
    build_plan_messages,
    to_crosscutting,
    to_plan_model,
)
from app.detailed_design.prompt_rules import NAMING_RULES

ROUTE = "app/api/routes/reservations.py"
_FUNCTIONS = FunctionListModel.model_validate(function_list_model()).functions


def _plan_output() -> PlanGenerationOutput:
    return PlanGenerationOutput(
        milestones=[
            GeneratedMilestone(
                name="予約",
                goal="登録できる",
                priority="Should",
                function_ids=["F-01"],
                tasks=[
                    GeneratedTask(
                        area="テスト", title="E2E", modules=[ROUTE], function_ids=["F-01"]
                    )
                ],
            )
        ],
        environment="uv",
        risks=[GeneratedRisk(risk="遅延", mitigation="削る")],
    )


def test_smoke_build_messages_and_convert_outputs():
    messages = build_crosscutting_messages("要件", "外部", "# 詳細設計書")
    assert messages[0].content == CROSSCUTTING_SYSTEM_PROMPT
    crosscutting = to_crosscutting(
        CrossCuttingGenerationOutput(
            crosscutting=[GeneratedCrossCutting(topic="認証", policy="JWT", modules=[ROUTE])]
        )
    )
    assert crosscutting == [CrossCuttingRow(topic="認証", policy="JWT", modules=[ROUTE])]
    model = to_plan_model(crosscutting, _plan_output())
    assert model.crosscutting == crosscutting
    [milestone] = model.milestones
    assert (milestone.name, milestone.priority, milestone.function_ids) == (
        "予約",
        "Should",
        ["F-01"],
    )
    assert milestone.tasks[0].area == "テスト"
    assert model.environment == "uv"
    assert model.risks[0].mitigation == "削る"


def test_crosscutting_messages_carry_three_documents():
    content = str(build_crosscutting_messages("要件", "外部", "# 詳細")[1].content)
    assert content == "## 要件定義書\n要件\n\n## 外部設計書\n外部\n\n## 詳細設計書\n# 詳細"


def test_plan_messages_carry_crosscutting_and_function_ids():
    rows = [CrossCuttingRow(topic="認証", policy="JWT")]
    messages = build_plan_messages("要件", "# 詳細", rows, _FUNCTIONS)
    assert messages[0].content == PLAN_SYSTEM_PROMPT
    content = str(messages[1].content)
    assert "## 横断事項\n- 認証: JWT" in content
    assert "## 処理ID の一覧\n- F-01: 予約を登録する" in content
    # 外部設計書は詳細設計書(01〜06章)に反映済みなので、計画には渡さない
    assert "外部設計書" not in content


def test_plan_messages_mark_empty_inputs():
    content = str(build_plan_messages("要件", "# 詳細", [], [])[1].content)
    assert "## 横断事項\n(ありません)" in content
    assert "## 処理ID の一覧\n(ありません)" in content


def test_system_prompts_name_default_topics_and_end_with_naming_rules():
    assert all(topic in CROSSCUTTING_SYSTEM_PROMPT for topic in CROSSCUTTING_TOPICS)
    assert CROSSCUTTING_SYSTEM_PROMPT.endswith(NAMING_RULES)
    assert PLAN_SYSTEM_PROMPT.endswith(NAMING_RULES)
    # ファイルの欄は例で、環境・設定のファイルも書ける
    assert "Dockerfile" in PLAN_SYSTEM_PROMPT


# --- E2E 用の固定の出力 ---


def test_e2e_fake_outputs_pass_stage7_validation():
    crosscutting = _UML_OUTPUTS[CrossCuttingGenerationOutput]
    plan = _UML_OUTPUTS[PlanGenerationOutput]
    assert isinstance(crosscutting, CrossCuttingGenerationOutput)
    assert isinstance(plan, PlanGenerationOutput)
    function_list = function_list_model()
    function_list["functions"].append(
        dict(function_list["functions"][0], id="F-02", trigger="GET /api/v1/reservations")
    )
    modules = module_list_model()
    for path in ("app/main.py", "app/services/reservation.py"):
        modules["modules"].append(dict(modules["modules"][0], path=path))
    paths = [m.path for m in ModuleListModel.model_validate(modules).modules]
    model = normalize_plan(to_plan_model(to_crosscutting(crosscutting), plan), paths)
    issues = validate_stage(
        7, model.model_dump(mode="json"), StageSources(stages={1: function_list, 4: modules})
    )
    assert issues == []
