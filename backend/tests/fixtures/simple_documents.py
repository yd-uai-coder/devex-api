"""内部設計書・実装計画書の構造化出力の見本(生成のテストで偽の LLM が返す)。

外部設計書の見本(`tests.fixtures.external_design.external_design_output`)の POST の API に
処理別データフローを1つ置き、実装計画書はその処理と要件定義書の見本の FR-001 を担うタスクを持つ。
"""

from app.ai.llm.fake import E2eFakeLLM
from app.documents.implementation_plan import build_model as build_plan_model
from app.documents.implementation_plan import render_implementation_plan
from app.documents.implementation_plan.drafting import (
    GeneratedMilestone,
    GeneratedTask,
    ImplementationPlanGenerationOutput,
    build_implementation_plan_messages,
)
from app.documents.implementation_plan.drafting import to_draft as to_plan_draft
from app.documents.internal_design import build_model, render_internal_design
from app.documents.internal_design.drafting import (
    GeneratedDataFlow,
    GeneratedFlow,
    GeneratedInternalApi,
    GeneratedModule,
    InternalDesignGenerationOutput,
    build_internal_design_messages,
    to_draft,
)


def internal_design_output(
    *, architecture: str = "内容", trigger: str = "POST /api/v1/reservations"
) -> InternalDesignGenerationOutput:
    """モジュール2つと、処理別データフロー1つ(`trigger`)。"""
    return InternalDesignGenerationOutput(
        architecture=architecture,
        data_model_note="なし",
        tables=[],
        module_policy="なし",
        apis=[GeneratedInternalApi(method="POST", path="/api/v1/reservations", summary="登録")],
        modules=[
            GeneratedModule(
                path="app/api/reservations.py",
                layer="api",
                responsibility="ルート",
                depends_on=["app/services/reservation.py"],
            ),
            GeneratedModule(
                path="app/services/reservation.py",
                layer="service",
                responsibility="登録",
                depends_on=[],
            ),
        ],
        dataflows=[
            GeneratedDataFlow(
                trigger=trigger,
                flows=[GeneratedFlow(source="利用者", data="予約", target="reservations")],
                data_items=[],
            )
        ],
        error_policy="なし",
    )


def implementation_plan_output(
    *, phases: str = "内容", title: str = "予約を登録する"
) -> ImplementationPlanGenerationOutput:
    """機能のタスク1つ(DF-1・FR-001)。"""
    return ImplementationPlanGenerationOutput(
        phases=phases,
        milestones=[
            GeneratedMilestone(
                name="予約",
                priority="Must",
                goal="予約できる",
                tasks=[
                    GeneratedTask(
                        id="M-01-T01",
                        kind="feature",
                        title=title,
                        dataflow_ids=["DF-1"],
                        req_ids=["FR-001"],
                        depends_on=[],
                        modules=["app/api/reservations.py"],
                        config_files=[],
                    )
                ],
            )
        ],
        environment="Docker",
        risks=[],
    )


async def e2e_fake_simple_documents(llm: E2eFakeLLM) -> dict[str, tuple[str, dict]]:
    """E2E 用の偽 LLM の内部設計書と実装計画書を、生成と同じく model → md にしたもの
    (文書の種類 → (md, model))。照合用の写し(API・処理の ID)は空のまま。"""
    messages = build_internal_design_messages("", "")
    output = await llm.with_structured_output(InternalDesignGenerationOutput).ainvoke(messages)
    assert isinstance(output, InternalDesignGenerationOutput)
    internal = build_model(to_draft(output), None)
    messages = build_implementation_plan_messages("", render_internal_design(internal))
    raw_plan = await llm.with_structured_output(ImplementationPlanGenerationOutput).ainvoke(
        messages
    )
    assert isinstance(raw_plan, ImplementationPlanGenerationOutput)
    plan = build_plan_model(to_plan_draft(raw_plan), None)
    return {
        "internal_design": (render_internal_design(internal), internal.model_dump(mode="json")),
        "implementation_plan": (render_implementation_plan(plan), plan.model_dump(mode="json")),
    }
