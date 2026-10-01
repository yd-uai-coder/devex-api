"""段階の下書きの生成(受け付け・実行・回収)と、生成・検証に関わる段階のAPIのテスト。

SUT: DesignStageGenerationService(request_generation / execute / recover_stale)、
     generate_function_list・STAGE_GENERATORS(app/services/design_stage_generation_service.py)、
     generate_design_stage / list_design_stages(app/api/routes/design_stages.py)、
     DesignStageService の生成中の保存の拒否、
     build_function_list_messages / to_drafts(app/detailed_design/drafting.py)、
     E2E用の偽LLMの段階1の出力(app/ai/llm/fake.py)
ドライバ: 各テスト関数(ルート関数・サービスのメソッドを直接呼ぶ)
スタブ: FakeLLM(tests/fixtures/fake_llm.py)── 構造化出力(Gemini)の代わり。
DBはインメモリSQLite(db_session)で、スタブにはしない(段階の行の状態の移り変わりそのものが検証対象のため)。
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project, function_list_model
from tests.fixtures.fake_llm import FakeLLM

from app.ai.llm.fake import E2eFakeLLM
from app.api.routes.design_stages import generate_design_stage, list_design_stages
from app.detailed_design import StageSources, validate_stage
from app.detailed_design.drafting import (
    FunctionListGenerationOutput,
    GeneratedFunction,
    build_function_list_messages,
    to_drafts,
)
from app.models.project import Project
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.services import llm_retry
from app.services.design_stage_generation_service import (
    STAGE_GENERATORS,
    DesignStageGenerationService,
    generate_function_list,
    run_design_stage_generation,
)
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    DesignStageGenerationInProgressError,
    DesignStageGenerationNotSupportedError,
    DesignStageLockedError,
)

EXTERNAL_DESIGN = (
    "# 2. 外部設計書\n\n## 2.6 API一覧\n| メソッド | パス | 概要 | 関連画面 |\n|---|---|---|---|\n"
    "| POST | /api/v1/reservations | 予約を登録する | SCR-001 |\n"
)


def _output(*names_and_triggers: tuple[str, str]) -> FunctionListGenerationOutput:
    return FunctionListGenerationOutput(
        functions=[
            GeneratedFunction(
                name=name, kind="API", trigger=trigger, screens=["SCR-001"], summary=""
            )
            for name, trigger in names_and_triggers
        ]
    )


async def _project(session: AsyncSession) -> Project:
    project = await create_detailed_project(session)
    await GeneratedDocumentRepository(session).create_version(
        project_id=project.id, doc_type="external_design", content=EXTERNAL_DESIGN
    )
    await session.commit()
    return project


async def _generate(session: AsyncSession, project: Project, llm) -> None:
    service = DesignStageGenerationService(session)
    await service.request_generation(project, stage=1)
    await service.execute(project_id=project.id, user_id=project.user_id, stage=1, llm=llm)


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_retry.asyncio, "sleep", _instant_sleep)


async def test_route_accepts_generation_and_execute_saves_draft(db_session: AsyncSession) -> None:
    """統合スモーク: ルートで受け付け → 実行で下書きを保存 → 一覧で生成状態と検証の結果を見る。"""
    project = await _project(db_session)
    tasks = BackgroundTasks()

    accepted = await generate_design_stage(1, db_session, project, tasks)
    await DesignStageGenerationService(db_session).execute(
        project_id=project.id,
        user_id=project.user_id,
        stage=1,
        llm=FakeLLM(structured=_output(("予約を登録する", "POST /api/v1/reservations"))),
    )
    [stage1, *_] = await list_design_stages(db_session, project)

    assert accepted.generation_status == "generating"
    assert [task.func for task in tasks.tasks] == [run_design_stage_generation]
    assert stage1.state == "draft"
    assert stage1.generation_status == "completed"
    assert stage1.version == 2
    assert stage1.model is not None
    assert [f["id"] for f in stage1.model["functions"]] == ["F-01"]
    assert stage1.issues == []


async def test_regeneration_keeps_ids_and_marks_stage_regenerated(
    db_session: AsyncSession,
) -> None:
    project = await _project(db_session)
    await _generate(
        db_session, project, FakeLLM(structured=_output(("予約", "POST /api/v1/reservations")))
    )
    service = DesignStageService(db_session)
    await service.approve(project, stage=1, expected_version=2)

    await _generate(
        db_session,
        project,
        FakeLLM(
            structured=_output(
                ("一覧", "GET /api/v1/reservations"), ("予約", "POST /api/v1/reservations")
            )
        ),
    )
    stage1 = await service.read(project.id, 1)

    assert stage1.state == "regenerated"
    assert stage1.version == 3
    assert stage1.model is not None
    assert [f["id"] for f in stage1.model["functions"]] == ["F-02", "F-01"]


async def test_regeneration_clears_outdated_and_later_input_change_marks_it_again(
    db_session: AsyncSession,
) -> None:
    """承認後に外部設計書が変わって「古い」になった段階を作り直すと、生成時の入力の版を記録し直すので
    「再生成済」になる。その後に外部設計書が変わると、また「古い」になる(Phase 16 の修正)。"""
    project = await _project(db_session)
    project_id = project.id
    llm = FakeLLM(structured=_output(("予約", "POST /api/v1/reservations")))
    await _generate(db_session, project, llm)
    service = DesignStageService(db_session)
    first = await service.read(project_id, 1)
    await service.approve(project, stage=1, expected_version=2)
    documents = GeneratedDocumentRepository(db_session)
    await documents.create_version(
        project_id=project_id, doc_type="external_design", content=EXTERNAL_DESIGN
    )
    await db_session.commit()
    outdated = await service.read(project_id, 1)

    await _generate(db_session, project, llm)
    regenerated = await service.read(project_id, 1)
    await documents.create_version(
        project_id=project_id, doc_type="external_design", content=EXTERNAL_DESIGN
    )
    await db_session.commit()
    changed = await service.read(project_id, 1)

    assert first.state == "draft"
    assert outdated.state == "outdated"
    assert regenerated.state == "regenerated"
    assert changed.state == "outdated"


async def test_failed_generation_records_reason(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _project(db_session)
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)

    project_id = project.id  # 失敗時の rollback で project は期限切れになるため、先に取っておく

    await _generate(db_session, project, FakeLLM(structured_sequence=[RuntimeError("429")]))
    stage1 = await DesignStageService(db_session).read(project_id, 1)

    assert stage1.generation_status == "failed"
    assert stage1.generation_error is not None
    assert "利用上限" in stage1.generation_error
    assert stage1.model is None


async def test_generation_rejects_unsupported_locked_and_running_stages(
    db_session: AsyncSession,
) -> None:
    project = await _project(db_session)
    service = DesignStageGenerationService(db_session)

    with pytest.raises(DesignStageGenerationNotSupportedError):
        await service.request_generation(project, stage=2)
    no_docs = await create_detailed_project(db_session, with_documents=False)
    with pytest.raises(DesignStageLockedError):
        await service.request_generation(no_docs, stage=1)

    await service.request_generation(project, stage=1)
    with pytest.raises(DesignStageGenerationInProgressError):
        await service.request_generation(project, stage=1)
    with pytest.raises(DesignStageGenerationInProgressError):
        await DesignStageService(db_session).save(
            project, stage=1, expected_version=1, model=function_list_model()
        )


async def test_stale_generation_is_recovered(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    await DesignStageGenerationService(db_session).request_generation(project, stage=1)
    row = await DesignStageRepository(db_session).get(project_id=project.id, stage=1)
    assert row is not None
    row.generation_started_at = datetime.now(UTC) - timedelta(minutes=16)
    await db_session.commit()

    [stage1, *_] = await list_design_stages(db_session, project)

    assert stage1.generation_status == "failed"
    assert stage1.generation_error is not None
    assert "時間内に終わらなかった" in stage1.generation_error


async def test_generate_function_list_reads_external_design() -> None:
    llm = FakeLLM(structured=_output(("予約", "POST /api/v1/reservations")))

    model = await generate_function_list(
        llm, StageSources(documents={"external_design": EXTERNAL_DESIGN}), None
    )

    assert STAGE_GENERATORS[1] is generate_function_list
    assert llm.structured_output_calls == [FunctionListGenerationOutput]
    assert model["groups"] == ["reservations"]


def test_messages_and_drafts() -> None:
    messages = build_function_list_messages(EXTERNAL_DESIGN)
    output = FunctionListGenerationOutput(
        functions=[
            GeneratedFunction(name="  ", kind="API", trigger="GET /x", screens=[], summary=""),
            GeneratedFunction(
                name="集計する", kind="バッチ", trigger="毎晩", screens=[" SCR-1 ", ""],
                summary="s", group_hint="運用",
            ),
        ]
    )

    drafts = to_drafts(output)

    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert EXTERNAL_DESIGN in str(messages[1].content)
    assert [(d.name, d.screens, d.group_hint) for d in drafts] == [("集計する", ("SCR-1",), "運用")]


async def test_e2e_fake_function_list_passes_validation() -> None:
    """偽LLM(E2E)の段階1の出力は、偽LLMの外部設計書と照らしてエラー・漏れなく通る。"""
    llm = E2eFakeLLM()
    external = str((await llm.ainvoke([SystemMessage(content="# 2. 外部設計書")])).content)
    sources = StageSources(documents={"external_design": external})

    model = await generate_function_list(llm, sources, None)

    assert validate_stage(1, model, sources) == []
