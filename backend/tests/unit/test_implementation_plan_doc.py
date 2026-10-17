"""実装計画書の model・組み立て・md・検証・下書きの入出力と、書かれた ID の読み替え(純粋関数)。

SUT: app/documents/implementation_plan の各関数、app/detailed_design/plan.renamed_task_ids。
ドライバ: このテスト。スタブ不要 ── 対象が純粋(副作用なし)で、LLM・DB を呼ばないため。
md が古い版の解析(`parse_wbs`)で同じ計画として読めることも、解析を素で呼んで確かめる。
"""

from app.detailed_design.plan import renamed_task_ids
from app.detailed_design.simple_procedure import parse_wbs
from app.documents.implementation_plan import (
    ImplementationPlanDraft,
    ImplementationPlanModel,
    MilestoneDraft,
    RiskDraft,
    TaskDraft,
    build_model,
    render_implementation_plan,
    row_counts,
    validate_implementation_plan,
)
from app.documents.implementation_plan.drafting import (
    GeneratedMilestone,
    GeneratedTask,
    ImplementationPlanGenerationOutput,
    build_implementation_plan_messages,
    to_draft,
)
from app.documents.requirements import RequirementRow, RequirementsModel


def _requirements() -> RequirementsModel:
    rows = [
        RequirementRow(id="FR-001", kind="FR", priority="Must", text="予約", source="user"),
        RequirementRow(id="FR-002", kind="FR", priority="Must", text="一覧", source="user"),
        RequirementRow(id="NFR-001", kind="NFR", category="性能", text="速い", source="user"),
    ]
    return RequirementsModel(purpose="目的", rows=rows)


def _draft(**kwargs) -> ImplementationPlanDraft:
    values = {
        "phases": "フェーズ1: M-01",
        "milestones": (
            MilestoneDraft(
                "予約の登録",
                "Must",
                "予約できる",
                (
                    TaskDraft("M-01-T01", "base", "開発環境", (), (), (), ("app/main.py",), (
                        "docker-compose.yml",
                    )),
                    # 書かれた ID が並び順と違う(依存先は書かれた ID で指す)
                    TaskDraft("M-01-T05", "feature", "予約を登録する", ("df-01",), ("fr-1",), (
                        "m-01-t01",
                    ), ("app/routers/reservations.py",)),
                    TaskDraft("M-01-T06", "feature", " ", ("DF-9",)),
                ),
            ),
            MilestoneDraft(" ", "Must", "", (TaskDraft("M-02-T01", "feature", "捨てる"),)),
            MilestoneDraft(
                "一覧",
                "Should",
                "",
                (TaskDraft("M-02-T01", "feature", "一覧を見る", ("DF-2",), ("FR-002", "x"), (
                    "M-01-T05",
                )),),
            ),
        ),
        "environment": "Docker",
        "risks": (RiskDraft("遅延", "後回し"), RiskDraft(" ")),
    }
    values.update(kwargs)
    return ImplementationPlanDraft(**values)  # type: ignore[arg-type]


def _model(**kwargs) -> ImplementationPlanModel:
    return build_model(
        _draft(**kwargs), None, dataflow_ids=["DF-1", "DF-2"], requirements=_requirements()
    )


def test_renamed_task_ids_maps_written_ids_to_positions() -> None:
    renames = renamed_task_ids([["M-01-T01", "m-01-t05", "M-01-T01"], ["M-02-T01"]])

    assert renames == {"M-01-T01": "M-01-T01", "M-01-T05": "M-01-T02", "M-02-T01": "M-02-T01"}


def test_build_model_normalizes_tasks_and_assigns_keys() -> None:
    model = _model()

    first, second = model.milestones
    assert [t.title for t in first.tasks] == ["開発環境", "予約を登録する"]
    task = first.tasks[1]
    assert (task.function_ids, task.req_ids, task.depends_on) == (["DF-1"], ["FR-001"], [
        "M-01-T01"
    ])
    assert first.tasks[0].kind == "base"
    assert first.tasks[0].config_files == ["docker-compose.yml"]
    # 書かれた M-01-T05 は並び順の M-01-T02 に読み替える。形の違う要件は捨てる
    assert second.tasks[0].depends_on == ["M-01-T02"]
    assert second.tasks[0].req_ids == ["FR-002"]
    assert second.priority == "Should"
    assert [t.key for m in model.milestones for t in m.tasks] == ["U-001", "U-002", "U-003"]
    assert model.next_unit_seq == 4
    assert [r.risk for r in model.risks] == ["遅延"]
    assert model.known_dataflow_ids == ["DF-1", "DF-2"]
    assert model.must_fr_ids == ["FR-001", "FR-002"]
    assert model.to_plan().milestones == model.milestones


def test_build_model_carries_over_keys_by_title() -> None:
    previous = _model()
    redrafted = _draft(
        milestones=(
            MilestoneDraft(
                "一覧",
                "Must",
                "",
                (
                    TaskDraft("M-01-T01", "feature", "一覧を見る", ("DF-2",)),
                    TaskDraft("M-01-T02", "feature", "通知する"),
                ),
            ),
        )
    )

    model = build_model(redrafted, previous)

    assert [t.key for t in model.milestones[0].tasks] == ["U-003", "U-004"]
    assert model.next_unit_seq == 5


def test_render_is_read_by_the_wbs_parser() -> None:
    model = _model()
    markdown = render_implementation_plan(model)

    parsed = parse_wbs(markdown)

    assert parsed.issues == ()
    plan = model.to_plan()
    # md はキーを持たない(並び順の ID とタスク名で同じ計画になる)
    assert [
        [t.model_copy(update={"key": ""}) for t in m.tasks] for m in plan.milestones
    ] == [m.tasks for m in parsed.plan.milestones]
    assert parsed.plan.environment == "Docker"
    assert "**リスク1: 遅延**" in markdown


def test_validation_reports_gaps() -> None:
    model = _model(
        milestones=(
            MilestoneDraft(
                "予約",
                "Must",
                "",
                (
                    TaskDraft("M-01-T01", "feature", "予約", (), ("FR-009",), ("M-01-T02",)),
                    TaskDraft("M-01-T02", "feature", "一覧", ("DF-7",), (), ("M-09-T01",)),
                ),
            ),
        )
    )

    issues = {i.code: i for i in validate_implementation_plan(model)}

    assert issues["FEATURE_WITHOUT_DATAFLOW"].targets == ("M-01-T01",)
    assert issues["UNKNOWN_DATAFLOW"].targets == ("DF-7",)
    assert issues["UNPLANNED_DATAFLOW"].targets == ("DF-1", "DF-2")
    assert issues["FORWARD_DEPENDENCY"].targets == ("M-01-T01 → M-01-T02",)
    assert issues["UNKNOWN_DEPENDENCY"].targets == ("M-01-T02 → M-09-T01",)
    assert issues["MUST_REQUIREMENT_UNPLANNED"].targets == ("FR-001", "FR-002")
    assert issues["UNLINKED_UNIT"].targets == ("M-01-T02",)
    assert issues["UNKNOWN_REQUIREMENT_ID"].targets == ("FR-009",)
    assert row_counts(model) == {"milestones": 1, "tasks": 2, "risks": 1}


def test_validation_reports_goal_without_task() -> None:
    model = _model(
        milestones=(
            MilestoneDraft(
                "基盤",
                "Must",
                "Docker と CI/CD・バックアップが整う",
                (TaskDraft("M-01-T01", "base", "Docker", (), (), (), (), ("Dockerfile",)),),
            ),
            MilestoneDraft(
                "予約",
                "Must",
                "予約できる",
                (TaskDraft("M-02-T01", "feature", "予約", ("DF-1", "DF-2"), ("FR-001", "FR-002")),),
            ),
        )
    )

    issues = {i.code: i for i in validate_implementation_plan(model)}

    assert issues["GOAL_WITHOUT_TASK"].level == "major"
    assert issues["GOAL_WITHOUT_TASK"].targets == ("M-01(CI/CD・バックアップ)",)


def test_validation_is_quiet_for_a_complete_plan_and_reports_no_tasks() -> None:
    assert validate_implementation_plan(_model()) == []
    issues = validate_implementation_plan(ImplementationPlanModel())
    assert [i.code for i in issues] == ["NO_TASKS"]


def test_drafting_converts_output_and_builds_messages() -> None:
    output = ImplementationPlanGenerationOutput(
        phases="p",
        milestones=[
            GeneratedMilestone(
                name="m",
                priority="Must",
                goal="g",
                tasks=[
                    GeneratedTask(
                        id="M-01-T01",
                        kind="feature",
                        title="t",
                        dataflow_ids=["DF-1"],
                        req_ids=["FR-001"],
                        depends_on=[],
                        modules=["app/x.py"],
                        config_files=[],
                    )
                ],
            )
        ],
        environment="e",
        risks=[],
    )

    draft = to_draft(output)
    messages = build_implementation_plan_messages("要件", "内部")

    assert draft.milestones[0].tasks[0] == TaskDraft(
        "M-01-T01", "feature", "t", ("DF-1",), ("FR-001",), (), ("app/x.py",), ()
    )
    assert "## 内部設計書\n内部" in messages[1].content
