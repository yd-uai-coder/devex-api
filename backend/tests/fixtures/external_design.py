"""外部設計書の構造化出力の見本と、上流の2文書の偽の構造化出力(生成のテストで偽の LLM が返す)。"""

from pydantic import BaseModel
from tests.fixtures.requirements import requirements_output

from app.ai.llm.fake import E2eFakeLLM
from app.documents.external_design import build_model, render_external_design
from app.documents.external_design.drafting import (
    ExternalDesignGenerationOutput,
    GeneratedApi,
    GeneratedScreen,
    build_external_design_messages,
    to_draft,
)
from app.documents.requirements.drafting import RequirementsGenerationOutput


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
) -> dict[type[BaseModel], BaseModel]:
    """FakeLLM の structured_by_schema に渡す、要件定義書と外部設計書の構造化出力。"""
    return {
        RequirementsGenerationOutput: requirements or requirements_output(),
        ExternalDesignGenerationOutput: external_design or external_design_output(),
    }


async def e2e_fake_external_design_md(llm: E2eFakeLLM) -> str:
    """E2E 用の偽 LLM が外部設計書の構造化出力に返す中身から、生成と同じく組み立てた md。"""
    messages = build_external_design_messages("# 1. 要件定義書", None)
    output = await llm.with_structured_output(ExternalDesignGenerationOutput).ainvoke(messages)
    assert isinstance(output, ExternalDesignGenerationOutput)
    return render_external_design(build_model(to_draft(output), None, None))
