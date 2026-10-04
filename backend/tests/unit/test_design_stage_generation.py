"""段階の下書きの生成(受け付け・実行・回収)と、生成・検証に関わる段階のAPIのテスト。

SUT: DesignStageGenerationService(request_generation / execute / recover_stale)、
     generate_function_list・generate_data_flow・STAGE_GENERATORS・StageGenerationContext
     (app/services/design_stage_generation_service.py)、
     DataItemService.resolve_by_name(app/services/data_item_service.py。段階2の生成から)、
     DesignStageService の入力(承認済みの段階の内容・DFD の要約)と段階2の承認、
     generate_design_stage / list_design_stages(app/api/routes/design_stages.py)、
     DesignStageService の生成中の保存の拒否、
     build_function_list_messages / to_drafts(app/detailed_design/drafting.py)、
     E2E用の偽LLMの段階1の出力(app/ai/llm/fake.py)
ドライバ: 各テスト関数(ルート関数・サービスのメソッドを直接呼ぶ)
スタブ: FakeLLM(tests/fixtures/fake_llm.py)── 構造化出力(Gemini)の代わり。段階2は1回の生成で
      処理概要表 → グループの DFD の順に呼ぶので、`structured_sequence`で順に返す。
DBはインメモリSQLite(db_session)で、スタブにはしない(段階の行の状態の移り変わりそのものが検証対象のため)。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import (
    create_detailed_project,
    data_flow_model,
    function_list_model,
)
from tests.fixtures.fake_llm import FakeLLM

from app.ai.llm.fake import E2eFakeLLM
from app.api.routes.design_stages import generate_design_stage, list_design_stages
from app.detailed_design import StageSources, validate_stage
from app.detailed_design.data_flow_drafting import (
    GeneratedGroupProcess,
    GeneratedSummary,
    GroupDfdGenerationOutput,
    ProcessSummaryGenerationOutput,
)
from app.detailed_design.drafting import (
    FunctionListGenerationOutput,
    GeneratedFunction,
    build_function_list_messages,
    to_drafts,
)
from app.models.project import Project
from app.repositories.data_item import DataItemRepository
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services import llm_retry
from app.services.data_item_service import DataItemService
from app.services.design_stage_generation_service import (
    STAGE_GENERATORS,
    DesignStageGenerationService,
    StageGenerationContext,
    generate_data_flow,
    generate_function_list,
    run_design_stage_generation,
)
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    DesignStageGenerationInProgressError,
    DesignStageGenerationNotSupportedError,
    DesignStageInvalidError,
    DesignStageLockedError,
)
from app.uml.domain import DataItemField
from app.uml.generation.schemas import GeneratedDataItem, GeneratedFlow, GeneratedNode

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
        await service.request_generation(project, stage=3)
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


def _context(
    session: AsyncSession, llm, sources: StageSources, previous: dict | None = None
) -> StageGenerationContext:
    return StageGenerationContext(
        llm=llm,
        sources=sources,
        previous=previous,
        fingerprint={},
        session=session,
        project_id=uuid.uuid4(),
    )


async def test_generate_function_list_reads_external_design(db_session: AsyncSession) -> None:
    llm = FakeLLM(structured=_output(("予約", "POST /api/v1/reservations")))
    sources = StageSources(documents={"external_design": EXTERNAL_DESIGN})

    model = await generate_function_list(_context(db_session, llm, sources))

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


async def test_e2e_fake_function_list_passes_validation(db_session: AsyncSession) -> None:
    """偽LLM(E2E)の段階1の出力は、偽LLMの外部設計書と照らしてエラー・漏れなく通る。"""
    llm = E2eFakeLLM()
    external = str((await llm.ainvoke([SystemMessage(content="# 2. 外部設計書")])).content)
    sources = StageSources(documents={"external_design": external})

    model = await generate_function_list(_context(db_session, llm, sources))

    assert validate_stage(1, model, sources) == []


# ---- 段階2(データフロー、Phase 17) ----


def _summary_output() -> ProcessSummaryGenerationOutput:
    return ProcessSummaryGenerationOutput(
        rows=[GeneratedSummary(function_id="F-01", input="予約", process="保存", output="予約")]
    )


def _dfd_output(*, item: str = "予約") -> GroupDfdGenerationOutput:
    return GroupDfdGenerationOutput(
        data_items=[GeneratedDataItem(name=item, fields=[])],
        processes=[GeneratedGroupProcess(function_id="F-01", description="保存", layer="受付")],
        external_entities=[GeneratedNode(id="e1", name="利用者")],
        data_stores=[GeneratedNode(id="s1", name="reservations")],
        flows=[
            GeneratedFlow(id="f1", source_id="e1", target_id="F-01", data_item_name=item),
            GeneratedFlow(id="f2", source_id="F-01", target_id="s1", data_item_name=item),
        ],
    )


async def _stage2_project(session: AsyncSession, *, dfd_groups: list[str]) -> Project:
    """段階1を承認し、段階2に DFD を描くグループを保存したプロジェクト。"""
    project = await create_detailed_project(session)
    service = DesignStageService(session)
    await service.save(project, stage=1, expected_version=None, model=function_list_model())
    await service.approve(project, stage=1, expected_version=1)
    model = {"dfd_groups": dfd_groups, "summaries": []}
    await service.save(project, stage=2, expected_version=None, model=model)
    return project


async def _generate_stage2(session: AsyncSession, project: Project, llm) -> None:
    service = DesignStageGenerationService(session)
    await service.request_generation(project, stage=2)
    await service.execute(project_id=project.id, user_id=project.user_id, stage=2, llm=llm)


async def test_stage2_generation_writes_summaries_dfd_and_data_items(
    db_session: AsyncSession,
) -> None:
    """統合スモーク(段階2): 処理概要表と、選んだグループの DFD・データ項目を書き、DFD を承認すると
    段階2を承認できる。"""
    project = await _stage2_project(db_session, dfd_groups=["reservations"])
    project_id = project.id
    llm = FakeLLM(structured_sequence=[_summary_output(), _dfd_output()])

    await _generate_stage2(db_session, project, llm)
    service = DesignStageService(db_session)
    stage2 = await service.read(project_id, 2)
    diagram = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project_id, notation="dfd", subject="reservations"
    )
    items = await DataItemRepository(db_session).list_for_project(project_id)

    assert STAGE_GENERATORS[2] is generate_data_flow
    assert llm.structured_output_calls == [
        ProcessSummaryGenerationOutput,
        GroupDfdGenerationOutput,
    ]
    assert stage2.state == "draft"
    assert stage2.model is not None
    assert stage2.model["dfd_groups"] == ["reservations"]
    assert [row["function_id"] for row in stage2.model["summaries"]] == ["F-01"]
    assert diagram is not None
    assert diagram.status == "draft"
    assert diagram.source_doc_versions == {"stage:1": 1, "doc:requirements": 1}
    assert [e["id"] for e in diagram.semantic_model["elements"]][0] == "F-01"
    assert [item.name for item in items] == ["予約"]
    assert [issue.code for issue in stage2.issues] == ["DFD_NOT_APPROVED"]

    diagram.status = "approved"
    await db_session.commit()
    approved = await service.approve(project, stage=2, expected_version=stage2.version or 0)
    assert approved.state == "approved"


async def test_stage2_regeneration_keeps_groups_and_overwrites_dfd(
    db_session: AsyncSession,
) -> None:
    project = await _stage2_project(db_session, dfd_groups=["reservations"])
    project_id = project.id
    await _generate_stage2(
        db_session, project, FakeLLM(structured_sequence=[_summary_output(), _dfd_output()])
    )
    repo = UmlDiagramRepository(db_session)
    first = await repo.get_by_subject(project_id=project_id, notation="dfd", subject="reservations")
    assert first is not None
    first.status = "approved"
    first_version = first.version
    await db_session.commit()

    await _generate_stage2(
        db_session,
        project,
        FakeLLM(structured_sequence=[_summary_output(), _dfd_output(item="予約内容")]),
    )
    stage2 = await DesignStageService(db_session).read(project_id, 2)
    diagrams = await repo.list_by_notation(project_id, "dfd")

    assert stage2.state == "regenerated"
    assert stage2.model is not None
    assert stage2.model["dfd_groups"] == ["reservations"]
    assert len(diagrams) == 1
    assert diagrams[0].status == "draft"
    assert diagrams[0].version == first_version + 1


async def test_stage2_failure_rolls_back_dfd_and_data_items(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _stage2_project(db_session, dfd_groups=["reservations"])
    project_id = project.id
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)

    await _generate_stage2(
        db_session, project, FakeLLM(structured_sequence=[_summary_output(), RuntimeError("429")])
    )
    stage2 = await DesignStageService(db_session).read(project_id, 2)

    assert stage2.generation_status == "failed"
    assert stage2.model == {"dfd_groups": ["reservations"], "summaries": []}
    assert await UmlDiagramRepository(db_session).list_by_notation(project_id, "dfd") == []
    assert await DataItemRepository(db_session).list_for_project(project_id) == []


async def test_stage2_rejects_too_many_groups_and_validates_with_stage1(
    db_session: AsyncSession,
) -> None:
    project = await _stage2_project(db_session, dfd_groups=[f"g{i}" for i in range(6)])

    with pytest.raises(DesignStageInvalidError):
        await DesignStageGenerationService(db_session).request_generation(project, stage=2)

    [_, stage2, *_] = await list_design_stages(db_session, project)
    codes = {issue.code for issue in stage2.issues}
    assert {"TOO_MANY_DFD_GROUPS", "UNKNOWN_DFD_GROUP", "MISSING_SUMMARY"} <= codes


async def test_stage2_without_dfd_groups_writes_only_summaries(db_session: AsyncSession) -> None:
    project = await _stage2_project(db_session, dfd_groups=[])
    project_id = project.id
    llm = FakeLLM(structured_sequence=[_summary_output()])

    await _generate_stage2(db_session, project, llm)
    stage2 = await DesignStageService(db_session).read(project_id, 2)

    assert llm.structured_output_calls == [ProcessSummaryGenerationOutput]
    assert stage2.model is not None
    assert stage2.model == data_flow_model() | {"summaries": stage2.model["summaries"]}
    assert stage2.issues == []


async def test_resolve_by_name_reuses_existing_and_creates_missing(
    db_session: AsyncSession,
) -> None:
    """データ項目の名前の解決(UML図の生成と段階2の生成で共有): 既存はフィールドを変えずに使い、
    無い名前だけを作る。commitしないので、呼び出し元の rollback で一緒に消える。"""
    project = await create_detailed_project(db_session)
    project_id = project.id
    service = DataItemService(db_session)
    existing = await service.create(project_id=project_id, name="予約", fields=[{"name": "id"}])

    ids = await service.resolve_by_name(
        project_id, {"予約": [DataItemField(name="別")], "備品": [DataItemField(name="code")]}
    )
    await db_session.rollback()
    items = await DataItemRepository(db_session).list_for_project(project_id)

    assert ids["予約"] == existing.id
    assert set(ids) == {"予約", "備品"}
    assert [(item.name, item.fields) for item in items] == [("予約", [{"name": "id"}])]
