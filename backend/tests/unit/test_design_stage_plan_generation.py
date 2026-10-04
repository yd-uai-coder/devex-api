"""段階7(横断事項と実装計画)の下書きの生成のテスト。

SUT: generate_plan・STAGE_GENERATORS[7](app/services/design_stage_generation_service.py)、
     generate_design_stage(app/api/routes/design_stages.py。段階7の受け付け)
ドライバ: 各テスト関数(ルート関数・サービスのメソッドを直接呼ぶ)
スタブ: FakeLLM(tests/fixtures/fake_llm.py)── 構造化出力(Gemini)の代わり。段階7は1回の生成で
      横断事項 → 実装計画の順に呼ぶので、`structured_sequence`で順に返す。
DBはインメモリSQLite(db_session)で、スタブにはしない(入力の詳細設計書を DB の段階と図から組み立てる
こと、図を`exported`にしないことが検証対象のため)。
"""

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_stage7_project
from tests.fixtures.fake_llm import FakeLLM

import app.services.design_stage_generation_service as generation_service
from app.api.routes.design_stages import generate_design_stage
from app.detailed_design import plan_drafting
from app.detailed_design.plan_drafting import (
    CrossCuttingGenerationOutput,
    GeneratedCrossCutting,
    GeneratedMilestone,
    GeneratedRisk,
    GeneratedTask,
    PlanGenerationOutput,
)
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services import llm_retry
from app.services.design_stage_generation_service import (
    STAGE_GENERATORS,
    DesignStageGenerationService,
    generate_plan,
)
from app.services.design_stage_service import DesignStageService

ROUTE = "app/api/routes/reservations.py"
TOPICS = ("例外と HTTP", "認証", "トランザクション", "ログ")


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_retry.asyncio, "sleep", _instant_sleep)


def _crosscutting_output() -> CrossCuttingGenerationOutput:
    # モジュールは短い書き方(区切り単位の部分一致でパスにそろう)
    return CrossCuttingGenerationOutput(
        crosscutting=[
            GeneratedCrossCutting(topic=t, policy=f"{t}の方針", modules=["routes/reservations"])
            for t in TOPICS
        ]
    )


def _plan_output() -> PlanGenerationOutput:
    return PlanGenerationOutput(
        milestones=[
            GeneratedMilestone(
                name=" 予約の登録 ",
                goal="予約を登録できる",
                priority="Must",
                function_ids=["F-01", "F-01"],
                tasks=[
                    GeneratedTask(
                        area="バックエンド",
                        title="予約の API",
                        modules=[ROUTE],
                        function_ids=["F-01"],
                    )
                ],
            )
        ],
        environment="uv と PostgreSQL",
        risks=[GeneratedRisk(risk="重複", mitigation="一意制約")],
    )


async def test_smoke_stage7_generates_plan_from_design_markdown_and_can_be_approved(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """統合スモーク(段階7): 受け付け → 横断事項 → 実装計画の順に下書き → そのまま承認できる。
    入力の詳細設計書は 01〜06章の md で、図は描かない(exported にしない)。"""
    prompts: list[str] = []

    def spy(*args, **kwargs):
        messages = plan_drafting.build_crosscutting_messages(*args, **kwargs)
        prompts.append(str(messages[1].content))
        return messages

    monkeypatch.setattr(generation_service, "build_crosscutting_messages", spy)
    project = await create_stage7_project(db_session)
    project_id = project.id
    llm = FakeLLM(structured_sequence=[_crosscutting_output(), _plan_output()])

    accepted = await generate_design_stage(7, db_session, project, BackgroundTasks())
    await DesignStageGenerationService(db_session).execute(
        project_id=project_id, user_id=project.user_id, stage=7, llm=llm
    )
    service = DesignStageService(db_session)
    stage7 = await service.read(project_id, 7)

    assert accepted.generation_status == "generating"
    assert STAGE_GENERATORS[7] is generate_plan
    assert llm.structured_output_calls == [CrossCuttingGenerationOutput, PlanGenerationOutput]
    [prompt] = prompts
    assert "## 要件定義書\n# requirements" in prompt
    assert "## 外部設計書\n# external_design" in prompt
    assert "## 06 処理ロジックの詳細" in prompt
    assert "07 横断事項" not in prompt
    assert "![" not in prompt  # 図は描かない
    assert stage7.state == "draft"
    model = stage7.model
    assert model is not None
    assert model["crosscutting"][0]["modules"] == [ROUTE]
    milestone = model["milestones"][0]
    assert (milestone["name"], milestone["function_ids"]) == ("予約の登録", ["F-01"])
    assert stage7.issues == []
    diagrams = await UmlDiagramRepository(db_session).list_for_project(project_id)
    assert {d.status for d in diagrams} == {"approved"}
    approved = await service.approve(project, stage=7, expected_version=stage7.version or 0)
    assert approved.state == "approved"


async def test_stage7_failure_keeps_previous_model(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)
    project = await create_stage7_project(db_session)
    project_id = project.id
    service = DesignStageGenerationService(db_session)
    await service.request_generation(project, stage=7)

    llm = FakeLLM(structured_sequence=[_crosscutting_output(), RuntimeError("429")])
    await service.execute(project_id=project_id, user_id=project.user_id, stage=7, llm=llm)
    stage7 = await DesignStageService(db_session).read(project_id, 7)

    assert stage7.generation_status == "failed"
    assert stage7.model is None
