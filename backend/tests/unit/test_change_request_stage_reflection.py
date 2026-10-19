"""反映済みの変更要求(CR)を段階の生成に渡す(段階の反映)のテスト。

SUT: DesignStageGenerationService.request_generation・execute の`cr_number`
     (app/services/design_stage_generation_service.py)、ChangeRequestService.record_stage_apply、
     DesignStageService.read の generation_change_request・generated_model
     (app/services/design_stage_service.py)、スキーマ DesignStageGenerate・
     GenerationChangeRequestRead、ルート generate_design_stage(app/api/routes/design_stages.py)
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)。設計は段階1〜8 を承認済みにした fixture
(`create_impact_project`)。CR は合意まで本物のサービスで進め、文書への反映は状態だけを
「反映済み」にして済ませる(文書の反映は test_change_request_reflection.py が確かめる)。
段階の生成の LLM は FakeLLM(段階1の構造化出力。プロンプトを記録する)
"""

from datetime import UTC, datetime

import pytest
from fastapi import BackgroundTasks
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.fake_llm import FakeLLM

from app.api.routes.design_stages import generate_design_stage
from app.change_requests.instruction import CR_INSTRUCTION_HEAD
from app.detailed_design.drafting import FunctionListGenerationOutput, GeneratedFunction
from app.models.project import Project
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import ChangeRequestCreate, ChangeRequestDecision
from app.schemas.design_stage import DesignStageGenerate, GenerationChangeRequestRead
from app.services.baseline_service import BaselineService
from app.services.change_request_service import ChangeRequestService
from app.services.design_stage_generation_service import (
    DesignStageGenerationService,
    run_design_stage_generation,
)
from app.services.design_stage_service import DesignStageService
from app.services.errors import ChangeRequestNotFoundError, ChangeRequestNotReflectableError

_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


async def _project_with_agreed_cr(session: AsyncSession) -> Project:
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    service = ChangeRequestService(session)
    await service.create(
        project,
        ChangeRequestCreate.model_validate(
            {
                "title": "登録で確認メールを送る",
                "content": "予約の登録の後に確認メールを送る",
                "background_source": "meeting",
                "targets": [{"requirement_id": "FR-001", "kind": "content"}],
            }
        ),
        created_by=None,
    )
    await service.explain(project, 1, actor_id=None)
    await service.decide(
        project,
        1,
        ChangeRequestDecision.model_validate(
            {"decision": "agreed", "decided_at": _AT, "reason": "合意"}
        ),
        actor_id=None,
    )
    return project


async def _mark_applied(session: AsyncSession, project: Project) -> None:
    """文書への反映を済ませたことにする(状態だけ)。"""
    cr = (await ChangeRequestService(session).get(project, 1)).change_request
    cr.status = "applied"
    await session.commit()


def _llm() -> FakeLLM:
    output = FunctionListGenerationOutput(
        functions=[
            GeneratedFunction(
                name="予約を登録する",
                kind="API",
                trigger="POST /api/v1/reservations",
                screens=["SCR-001"],
                summary="",
            )
        ]
    )
    return FakeLLM(structured_sequence=[output])


async def test_stage_generation_with_cr_records_the_reflection(db_session: AsyncSession) -> None:
    project = await _project_with_agreed_cr(db_session)
    await _mark_applied(db_session, project)
    llm = _llm()
    service = DesignStageGenerationService(db_session)

    await service.request_generation(project, stage=1, cr_number=1)
    await service.execute(
        project_id=project.id, user_id=project.user_id, stage=1, cr_number=1, llm=llm
    )

    human = str(llm.structured_messages[0][-1].content)
    assert f"## 修正指示\n{CR_INSTRUCTION_HEAD.format(number=1)}" in human
    read = await DesignStageService(db_session).read(project.id, 1)
    assert read.generation_change_request == GenerationChangeRequestRead(
        number=1, label="CR-1", title="登録で確認メールを送る"
    )
    assert read.generated_model is not None
    cr = (await ChangeRequestService(db_session).get(project, 1)).change_request
    last = cr.events[-1]
    assert (last.action, last.from_status, last.to_status) == ("apply", "applied", "applied")
    assert last.refs == {"stage": 1, "version": read.version, "model": read.generated_model}
    assert last.note == "段階1の下書きを作り直した"


async def test_stage_generation_requires_applied_cr(db_session: AsyncSession) -> None:
    project = await _project_with_agreed_cr(db_session)
    service = DesignStageGenerationService(db_session)

    with pytest.raises(ChangeRequestNotReflectableError):  # 合意だけでは、まだ文書へ反映していない
        await service.request_generation(project, stage=1, cr_number=1)
    with pytest.raises(ChangeRequestNotFoundError):
        await service.request_generation(project, stage=1, cr_number=7)
    read = await DesignStageService(db_session).read(project.id, 1)
    assert read.generation_status != "generating"


async def test_stage_generation_without_cr_clears_the_link(db_session: AsyncSession) -> None:
    project = await _project_with_agreed_cr(db_session)
    await _mark_applied(db_session, project)
    service = DesignStageGenerationService(db_session)
    await service.request_generation(project, stage=1, cr_number=1)
    await service.execute(
        project_id=project.id, user_id=project.user_id, stage=1, cr_number=1, llm=_llm()
    )

    await service.request_generation(project, stage=1)
    await service.execute(project_id=project.id, user_id=project.user_id, stage=1, llm=_llm())

    read = await DesignStageService(db_session).read(project.id, 1)
    assert read.generation_change_request is None
    cr = (await ChangeRequestService(db_session).get(project, 1)).change_request
    assert [e.action for e in cr.events].count("apply") == 1


async def test_route_passes_cr_number(db_session: AsyncSession) -> None:
    project = await _project_with_agreed_cr(db_session)
    await _mark_applied(db_session, project)
    tasks = BackgroundTasks()

    await generate_design_stage(
        1, db_session, project, tasks, DesignStageGenerate(cr_number=1)
    )

    [task] = tasks.tasks
    assert task.func is run_design_stage_generation
    assert task.args[-1] == 1


def test_generate_payload_cr_number_is_positive() -> None:
    assert DesignStageGenerate().cr_number is None
    with pytest.raises(ValidationError):
        DesignStageGenerate.model_validate({"cr_number": 0})
