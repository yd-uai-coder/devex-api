"""外部設計書の構造化出力の見本と、4文書の偽の構造化出力(生成のテストで偽の LLM が返す)。"""

from pydantic import BaseModel
from tests.fixtures.requirements import requirements_output
from tests.fixtures.simple_documents import implementation_plan_output, internal_design_output
from tests.fixtures.spec_documents import spec_output

from app.ai.llm.fake import E2eFakeLLM
from app.documents.external_design import build_model, render_external_design
from app.documents.external_design.drafting import (
    ExternalDesignGenerationOutput,
    GeneratedApi,
    GeneratedScreen,
    build_external_design_messages,
    to_draft,
)
from app.documents.implementation_plan.drafting import ImplementationPlanGenerationOutput
from app.documents.internal_design.drafting import InternalDesignGenerationOutput
from app.documents.requirements import build_model as build_requirements_model
from app.documents.requirements import render_requirements
from app.documents.requirements.drafting import (
    RequirementsGenerationOutput,
    build_requirements_messages,
)
from app.documents.requirements.drafting import to_draft as to_requirements_draft
from app.documents.test_spec.drafting import TestSpecGenerationOutput


def external_design_output(
    *, screen_id: str = "SCR-001", overview: str = "内容"
) -> ExternalDesignGenerationOutput:
    """画面1つ(FR-001)と、本文の無い POST の API 1つ(要件定義書の見本の FR-001 に結ぶ)。"""
    return ExternalDesignGenerationOutput(
        overview=overview,
        screens=[
            GeneratedScreen(
                id=screen_id,
                name="予約画面",
                role="予約する",
                priority="Must",
                req_ids=["FR-001"],
                ui_spec="- 入力: 日付",
            )
        ],
        transition="SCR-001",
        integrations=[],
        data_io="なし",
        apis=[
            GeneratedApi(
                method="POST",
                path="/api/v1/reservations",
                summary="予約を登録する",
                screens=[screen_id],
                req_ids=["FR-001"],
                response="201 予約",
            )
        ],
    )


def upstream_outputs(
    requirements: RequirementsGenerationOutput | None = None,
    external_design: ExternalDesignGenerationOutput | None = None,
    internal_design: InternalDesignGenerationOutput | None = None,
    implementation_plan: ImplementationPlanGenerationOutput | None = None,
    test_spec: TestSpecGenerationOutput | None = None,
) -> dict[type[BaseModel], BaseModel]:
    """FakeLLM の structured_by_schema に渡す、4文書とテスト仕様書の構造化出力(詳細設計モードは
    前の2つと、段階7の承認の後のテスト仕様書を使う)。"""
    return {
        RequirementsGenerationOutput: requirements or requirements_output(),
        ExternalDesignGenerationOutput: external_design or external_design_output(),
        InternalDesignGenerationOutput: internal_design or internal_design_output(),
        ImplementationPlanGenerationOutput: implementation_plan or implementation_plan_output(),
        TestSpecGenerationOutput: test_spec or spec_output(),
    }


async def e2e_fake_external_design_md(llm: E2eFakeLLM) -> str:
    """E2E 用の偽 LLM が外部設計書の構造化出力に返す中身から、生成と同じく組み立てた md。"""
    messages = build_external_design_messages("# 1. 要件定義書", None)
    output = await llm.with_structured_output(ExternalDesignGenerationOutput).ainvoke(messages)
    assert isinstance(output, ExternalDesignGenerationOutput)
    return render_external_design(build_model(to_draft(output), None, None))


async def e2e_fake_upstream(llm: E2eFakeLLM) -> dict[str, tuple[str, dict]]:
    """E2E 用の偽 LLM の要件定義書と外部設計書を、生成と同じく model → md にしたもの
    (文書の種類 → (md, model))。外部設計書は要件定義書の要件ID を写す(要件の追跡が読む)。"""
    messages = build_requirements_messages("", {}, None, None)
    output = await llm.with_structured_output(RequirementsGenerationOutput).ainvoke(messages)
    assert isinstance(output, RequirementsGenerationOutput)
    requirements = build_requirements_model(to_requirements_draft(output), None, {}, None)
    messages = build_external_design_messages(render_requirements(requirements), None)
    external = await llm.with_structured_output(ExternalDesignGenerationOutput).ainvoke(messages)
    assert isinstance(external, ExternalDesignGenerationOutput)
    design = build_model(to_draft(external), None, requirements)
    return {
        "requirements": (render_requirements(requirements), requirements.model_dump(mode="json")),
        "external_design": (render_external_design(design), design.model_dump(mode="json")),
    }
