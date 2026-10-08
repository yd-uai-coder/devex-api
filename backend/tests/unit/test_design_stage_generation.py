"""段階の下書きの生成(受け付け・実行・回収)と、生成・検証に関わる段階のAPIのテスト。

SUT: DesignStageGenerationService(request_generation / execute / recover_stale)、
     generate_function_list・generate_data_flow・generate_data_model・generate_structure・
     generate_procedures・generate_logics・STAGE_GENERATORS・
     StageGenerationContext(app/services/design_stage_generation_service.py)、
     DataItemService.resolve_by_name(app/services/data_item_service.py。段階2の生成から)、
     DesignStageService の入力(承認済みの段階の内容・DFD・ER・構成図の要約)と段階2〜4の承認
     (段階3は承認で下書きの印を外す)、段階3の`dfd_accesses`(app/schemas/design_stage.py)、
     generate_design_stage / list_design_stages(app/api/routes/design_stages.py)、
     DesignStageGenerate・LogicTarget(app/schemas/design_stage.py。段階5・6の生成の対象)、
     DesignStageService の生成中の保存の拒否、
     build_function_list_messages / to_drafts(app/detailed_design/drafting.py)、
     E2E用の偽LLMの段階1の出力(app/ai/llm/fake.py)
ドライバ: 各テスト関数(ルート関数・サービスのメソッドを直接呼ぶ)
スタブ: FakeLLM(tests/fixtures/fake_llm.py)── 構造化出力(Gemini)の代わり。段階2は1回の生成で
      処理概要表 → グループの DFD の順に、段階3は ER → CRUD 図の順に、段階4は構成図 → モジュール一覧
      の順に呼ぶので、`structured_sequence`で順に返す。段階5は対象の処理ごとに、段階6は対象の
      関数ごとに1回呼ぶ。
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
    create_stage3_project,
    create_stage4_project,
    create_stage5_project,
    create_stage6_project,
    data_flow_model,
    function_list_model,
)
from tests.fixtures.fake_llm import FakeLLM

from app.ai.llm.fake import E2eFakeLLM
from app.api.routes.design_stages import generate_design_stage, list_design_stages
from app.detailed_design import (
    StageSources,
    logic_drafting,
    procedure_drafting,
    structure_drafting,
    validate_stage,
)
from app.detailed_design.data_flow_drafting import (
    GeneratedGroupProcess,
    GeneratedSummary,
    GroupDfdGenerationOutput,
    ProcessSummaryGenerationOutput,
)
from app.detailed_design.data_model_drafting import (
    CrudGenerationOutput,
    DataModelErOutput,
    DraftedColumn,
    DraftedTable,
    GeneratedCrudCell,
)
from app.detailed_design.drafting import (
    FunctionListGenerationOutput,
    GeneratedFunction,
    build_function_list_messages,
    to_drafts,
)
from app.detailed_design.logic_drafting import GeneratedPseudoStep, LogicGenerationOutput
from app.detailed_design.procedure_drafting import GeneratedStep, ProcedureGenerationOutput
from app.detailed_design.structure_drafting import (
    GeneratedModuleRow,
    ModuleListGenerationOutput,
)
from app.models.project import Project
from app.repositories.data_item import DataItemRepository
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.schemas.design_stage import DesignStageGenerate, DfdAccessRead, LogicTarget
from app.services import design_stage_generation_service as generation_service
from app.services import llm_retry
from app.services.data_item_service import DataItemService
from app.services.design_stage_generation_service import (
    STAGE_GENERATORS,
    DesignStageGenerationService,
    StageGenerationContext,
    generate_data_flow,
    generate_data_model,
    generate_function_list,
    generate_logics,
    generate_procedures,
    generate_structure,
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
from app.uml.generation.schemas import (
    ComponentGenerationOutput,
    GeneratedDataItem,
    GeneratedDependency,
    GeneratedFlow,
    GeneratedModule,
    GeneratedNode,
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
    「再生成済」になる。その後に外部設計書が変わると、また「古い」になる。"""
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
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _project(db_session)
    service = DesignStageGenerationService(db_session)

    # 段階1〜7のすべてに生成があるので、登録を外して「未対応」の断りを確かめる
    with monkeypatch.context() as patch:
        patch.delitem(STAGE_GENERATORS, 1)
        with pytest.raises(DesignStageGenerationNotSupportedError):
            await service.request_generation(project, stage=1)
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
                name="集計する",
                kind="バッチ",
                trigger="毎晩",
                screens=[" SCR-1 ", ""],
                summary="s",
                group_hint="運用",
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


# ---- 段階2(データフロー) ----


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
    # DFD を描くグループを選んでいないので、その警告だけが出る
    assert [issue.code for issue in stage2.issues] == ["NO_DFD_GROUPS"]


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


# ---- 段階3(データモデル) ----


def _er_output() -> DataModelErOutput:
    return DataModelErOutput(
        tables=[
            DraftedTable(
                id="t1",
                name="reservations",
                columns=[
                    DraftedColumn(
                        name="id",
                        type="UUID",
                        is_primary_key=True,
                        is_foreign_key=False,
                        nullable=False,
                        description="予約ID",
                    )
                ],
            )
        ],
        relations=[],
    )


def _crud_output(ops: str = "C") -> CrudGenerationOutput:
    return CrudGenerationOutput(
        cells=[GeneratedCrudCell(function_id="F-01", table="reservations", ops=ops)]
    )


async def _generate_stage3(session: AsyncSession, project: Project, llm) -> None:
    service = DesignStageGenerationService(session)
    await service.request_generation(project, stage=3)
    await service.execute(project_id=project.id, user_id=project.user_id, stage=3, llm=llm)


async def test_stage3_generation_writes_er_and_crud_and_approval_confirms_drafts(
    db_session: AsyncSession,
) -> None:
    """統合スモーク(段階3): ER と CRUD 図を書き、ER を承認すると段階3を承認でき、承認で CRUD 図の
    下書きの印が外れる。"""
    project = await create_stage3_project(db_session)
    project_id = project.id
    llm = FakeLLM(structured_sequence=[_er_output(), _crud_output()])

    await _generate_stage3(db_session, project, llm)
    service = DesignStageService(db_session)
    stage3 = await service.read(project_id, 3)
    er = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project_id, notation="er", subject=""
    )

    assert STAGE_GENERATORS[3] is generate_data_model
    assert llm.structured_output_calls == [DataModelErOutput, CrudGenerationOutput]
    assert stage3.state == "draft"
    assert stage3.model == {
        "cells": [{"function_id": "F-01", "table": "reservations", "ops": "C", "draft": True}]
    }
    assert stage3.dfd_accesses == [
        DfdAccessRead(function_id="F-01", table="reservations", kind="write")
    ]
    assert er is not None
    assert er.status == "draft"
    assert er.source_doc_versions == {"stage:2": 1}
    assert er.semantic_model["elements"][0]["columns"][0]["description"] == "予約ID"
    assert [issue.code for issue in stage3.issues] == ["ER_NOT_APPROVED", "DRAFT_CELLS"]

    er.status = "approved"
    await db_session.commit()
    approved = await service.approve(project, stage=3, expected_version=stage3.version or 0)
    assert approved.state == "approved"
    assert approved.model is not None
    assert approved.model["cells"][0]["draft"] is False
    assert approved.issues == []


async def test_stage3_regeneration_overwrites_er_and_replaces_crud(
    db_session: AsyncSession,
) -> None:
    project = await create_stage3_project(db_session)
    project_id = project.id
    await _generate_stage3(
        db_session, project, FakeLLM(structured_sequence=[_er_output(), _crud_output()])
    )
    repo = UmlDiagramRepository(db_session)
    first = await repo.get_by_subject(project_id=project_id, notation="er", subject="")
    assert first is not None
    first.status = "approved"
    first_version = first.version
    await db_session.commit()

    await _generate_stage3(
        db_session, project, FakeLLM(structured_sequence=[_er_output(), _crud_output("CU")])
    )
    stage3 = await DesignStageService(db_session).read(project_id, 3)
    diagrams = await repo.list_by_notation(project_id, "er")

    assert stage3.state == "regenerated"
    assert stage3.model is not None
    assert [cell["ops"] for cell in stage3.model["cells"]] == ["CU"]
    assert len(diagrams) == 1
    assert (diagrams[0].status, diagrams[0].version) == ("draft", first_version + 1)


async def test_stage3_failure_rolls_back_er(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_stage3_project(db_session)
    project_id = project.id
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)

    await _generate_stage3(
        db_session, project, FakeLLM(structured_sequence=[_er_output(), RuntimeError("429")])
    )
    stage3 = await DesignStageService(db_session).read(project_id, 3)

    assert stage3.generation_status == "failed"
    assert stage3.model is None
    assert await UmlDiagramRepository(db_session).list_by_notation(project_id, "er") == []


# ---- 段階4(ソフトウェア構造) ----


def _component_output(layer: str = "api") -> ComponentGenerationOutput:
    return ComponentGenerationOutput(
        modules=[
            GeneratedModule(id="m1", name="api/routes", description="HTTP の境界", layer=layer),
            GeneratedModule(id="m2", name="services", description="ユースケース", layer="service"),
        ],
        dependencies=[GeneratedDependency(id="d1", source_id="m1", target_id="m2")],
    )


def _module_output(*functions: str) -> ModuleListGenerationOutput:
    return ModuleListGenerationOutput(
        modules=[
            GeneratedModuleRow(
                path="app/api/routes/reservations.py",
                layer="api",
                responsibility="予約の API",
                depends_on=["app/services/reservation.py"],
                functions=list(functions or ("F-01",)),
                all_functions=False,
            ),
            GeneratedModuleRow(
                path="app/services/reservation.py",
                layer="service",
                responsibility="予約の登録",
                depends_on=[],
                functions=["F-01"],
                all_functions=False,
            ),
        ]
    )


async def _generate_stage4(session: AsyncSession, project: Project, llm) -> None:
    service = DesignStageGenerationService(session)
    await service.request_generation(project, stage=4)
    await service.execute(project_id=project.id, user_id=project.user_id, stage=4, llm=llm)


async def test_stage4_generation_writes_component_and_modules_and_can_be_approved(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """統合スモーク(段階4): 構成図とモジュール一覧を書き、構成図を承認すると段階4を承認できる。
    モジュール一覧の下書きには、構成図・CRUD 図・ER のテーブルを渡す。"""
    prompts: list[str] = []

    def spy(*args, **kwargs):
        messages = structure_drafting.build_module_messages(*args, **kwargs)
        prompts.append(str(messages[1].content))
        return messages

    monkeypatch.setattr(generation_service, "build_module_messages", spy)
    project = await create_stage4_project(db_session)
    project_id = project.id
    llm = FakeLLM(structured_sequence=[_component_output(), _module_output("F-01", "F-99")])

    await _generate_stage4(db_session, project, llm)
    service = DesignStageService(db_session)
    stage4 = await service.read(project_id, 4)
    component = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project_id, notation="component", subject=""
    )

    assert STAGE_GENERATORS[4] is generate_structure
    assert llm.structured_output_calls == [ComponentGenerationOutput, ModuleListGenerationOutput]
    [module_prompt] = prompts
    assert "- api/routes(層: api)" in module_prompt
    assert "- F-01 × reservations: C" in module_prompt
    assert "## テーブル\n- reservations" in module_prompt
    assert stage4.state == "draft"
    assert stage4.model is not None
    # 機能一覧に無い F-99 は捨てる
    assert [row["functions"] for row in stage4.model["modules"]] == [["F-01"], ["F-01"]]
    assert component is not None
    assert component.status == "draft"
    assert component.view == "structure"
    assert component.source_doc_versions == {
        "stage:1": 1,
        "stage:2": 1,
        "stage:3": 1,
        "doc:requirements": 1,
    }
    assert [issue.code for issue in stage4.issues] == ["COMPONENT_NOT_APPROVED"]

    component.status = "approved"
    await db_session.commit()
    approved = await service.approve(project, stage=4, expected_version=stage4.version or 0)
    assert approved.state == "approved"
    assert approved.issues == []


async def test_stage4_regeneration_overwrites_component_and_replaces_modules(
    db_session: AsyncSession,
) -> None:
    project = await create_stage4_project(db_session)
    project_id = project.id
    await _generate_stage4(
        db_session, project, FakeLLM(structured_sequence=[_component_output(), _module_output()])
    )
    repo = UmlDiagramRepository(db_session)
    first = await repo.get_by_subject(project_id=project_id, notation="component", subject="")
    assert first is not None
    first.status = "approved"
    first_version = first.version
    await db_session.commit()

    await _generate_stage4(
        db_session,
        project,
        FakeLLM(structured_sequence=[_component_output("入口"), _module_output()]),
    )
    stage4 = await DesignStageService(db_session).read(project_id, 4)
    diagrams = await repo.list_by_notation(project_id, "component")

    assert stage4.state == "regenerated"
    assert len(diagrams) == 1
    assert (diagrams[0].status, diagrams[0].version) == ("draft", first_version + 1)
    # 構成図の層が「入口」に変わったので、モジュール一覧の層 api は警告になる
    assert "UNKNOWN_LAYER" in [issue.code for issue in stage4.issues]


async def test_stage4_failure_rolls_back_component(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_stage4_project(db_session)
    project_id = project.id
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)

    await _generate_stage4(
        db_session,
        project,
        FakeLLM(structured_sequence=[_component_output(), RuntimeError("429")]),
    )
    stage4 = await DesignStageService(db_session).read(project_id, 4)

    assert stage4.generation_status == "failed"
    assert stage4.model is None
    assert await UmlDiagramRepository(db_session).list_by_notation(project_id, "component") == []


# --- 段階5 主要処理の手順 ---

ROUTE = "app/api/routes/reservations.py"


def _step(callee: str = "routes/reservations", *, action: str = "検証する") -> GeneratedStep:
    return GeneratedStep(
        caller="利用者",
        callee=callee,
        call="create_reservation",
        data="予約リクエスト",
        action=action,
        result="予約",
        db="reservations C",
        branch="—",
        is_branch=False,
    )


def _procedure_output(action: str = "検証する") -> ProcedureGenerationOutput:
    return ProcedureGenerationOutput(reason="AIの理由", note="注記", steps=[_step(action=action)])


async def _select(session: AsyncSession, project: Project, procedures: list[dict]) -> None:
    """段階5の手順を書く処理を選んで保存する(画面の「選択を保存」と同じ)。"""
    row = await DesignStageService(session).read(project.id, 5)
    await DesignStageService(session).save(
        project, stage=5, expected_version=row.version, model={"procedures": procedures}
    )


async def test_stage5_route_generates_pending_procedure_and_can_be_approved(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """統合スモーク(段階5): 本文なしで受け付け → 手順の無い処理を下書き → そのまま承認できる。
    呼び出し先はモジュール一覧のパスにそろい、下書きには段階3・4の内容を渡す。"""
    prompts: list[str] = []

    def spy(*args, **kwargs):
        messages = procedure_drafting.build_procedure_messages(*args, **kwargs)
        prompts.append(str(messages[1].content))
        return messages

    monkeypatch.setattr(generation_service, "build_procedure_messages", spy)
    project = await create_stage5_project(db_session)
    project_id = project.id
    await _select(db_session, project, [{"function_id": "F-01", "reason": "人の理由"}])
    tasks = BackgroundTasks()
    llm = FakeLLM(structured=_procedure_output())

    accepted = await generate_design_stage(5, db_session, project, tasks)
    await DesignStageGenerationService(db_session).execute(
        project_id=project_id, user_id=project.user_id, stage=5, llm=llm
    )
    service = DesignStageService(db_session)
    stage5 = await service.read(project_id, 5)

    assert accepted.generation_status == "generating"
    # 対象の指定なし → 実行時に手順の無い処理を選ぶ
    assert tasks.tasks[0].args[3:] == (None, None, None)
    assert STAGE_GENERATORS[5] is generate_procedures
    assert llm.structured_output_calls == [ProcedureGenerationOutput]
    [prompt] = prompts
    assert "- reservations: C" in prompt
    assert f"- {ROUTE}(層: api" in prompt
    assert stage5.state == "draft"
    assert stage5.model is not None
    [procedure] = stage5.model["procedures"]
    assert procedure["reason"] == "人の理由"
    assert procedure["steps"][0]["callee"] == ROUTE
    assert stage5.issues == []
    approved = await service.approve(project, stage=5, expected_version=stage5.version or 0)
    assert approved.state == "approved"


async def test_stage5_regenerates_only_requested_procedure(db_session: AsyncSession) -> None:
    project = await create_stage5_project(db_session)
    project_id = project.id
    human = {"function_id": "F-02", "reason": "人", "steps": [{"callee": "利用者"}]}
    await _select(db_session, project, [{"function_id": "F-01"}, human])
    service = DesignStageGenerationService(db_session)
    # 手順の無い F-01 だけを下書き(F-02 は手順があるので対象外)
    await service.request_generation(project, stage=5)
    first = FakeLLM(structured=_procedure_output("1回目"))
    await service.execute(project_id=project_id, user_id=project.user_id, stage=5, llm=first)
    assert (await DesignStageService(db_session).read(project_id, 5)).state == "draft"

    payload = DesignStageGenerate(function_ids=["F-01"])
    tasks = BackgroundTasks()
    await generate_design_stage(5, db_session, project, tasks, payload)
    await service.execute(
        project_id=project_id,
        user_id=project.user_id,
        stage=5,
        function_ids=["F-01"],
        llm=FakeLLM(structured=_procedure_output("2回目")),
    )
    stage5 = await DesignStageService(db_session).read(project_id, 5)

    assert first.structured_output_calls == [ProcedureGenerationOutput]
    assert tasks.tasks[0].args[3] == ["F-01"]
    assert stage5.state == "regenerated"
    assert stage5.model is not None
    f01, f02 = stage5.model["procedures"]
    assert f01["steps"][0]["action"] == "2回目"
    assert f02["steps"] == [{**f02["steps"][0], "callee": "利用者"}]
    assert f02["reason"] == "人"


async def test_stage5_rejects_missing_unselected_and_too_many_targets(
    db_session: AsyncSession,
) -> None:
    project = await create_stage5_project(db_session)
    service = DesignStageGenerationService(db_session)

    with pytest.raises(DesignStageInvalidError, match="下書きを作る処理がありません"):
        await service.request_generation(project, stage=5)
    await _select(db_session, project, [{"function_id": f"F-0{n}"} for n in range(1, 7)])
    with pytest.raises(DesignStageInvalidError, match="選ばれていない処理です: F-09"):
        await service.request_generation(project, stage=5, function_ids=["F-01", "F-09"])
    with pytest.raises(DesignStageInvalidError, match="5 つまで"):
        await service.request_generation(project, stage=5)
    with pytest.raises(DesignStageInvalidError, match="段階5だけ"):
        await service.request_generation(project, stage=4, function_ids=["F-01"])
    # 5つ以内に絞れば受け付ける
    accepted = await service.request_generation(
        project, stage=5, function_ids=["F-01", " F-01 ", "F-02"]
    )
    assert accepted.generation_status == "generating"


async def test_stage5_failure_keeps_previous_procedures(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_stage5_project(db_session)
    project_id = project.id
    human = [{"caller": "利用者", "callee": ROUTE, "call": "f", "action": "人の手直し"}]
    await _select(db_session, project, [{"function_id": "F-01", "steps": human}])
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)
    service = DesignStageGenerationService(db_session)

    await service.request_generation(project, stage=5, function_ids=["F-01"])
    await service.execute(
        project_id=project_id,
        user_id=project.user_id,
        stage=5,
        function_ids=["F-01"],
        llm=FakeLLM(structured_sequence=[RuntimeError("429")]),
    )
    stage5 = await DesignStageService(db_session).read(project_id, 5)

    assert stage5.generation_status == "failed"
    assert stage5.model is not None
    assert stage5.model["procedures"][0]["steps"][0]["action"] == "人の手直し"


async def test_generate_procedures_skips_function_removed_from_stage1(
    db_session: AsyncSession,
) -> None:
    """受け付けの後に段階1から消えた処理は、LLM を呼ばずに飛ばす
    (検証の UNKNOWN_FUNCTION で知らせる)。"""
    llm = FakeLLM(structured=_procedure_output())
    context = StageGenerationContext(
        llm=llm,
        sources=StageSources(stages={1: function_list_model()}),
        previous={"procedures": [{"function_id": "F-09"}]},
        fingerprint={},
        session=db_session,
        project_id=uuid.uuid4(),
        targets=("F-09",),
    )

    model = await generate_procedures(context)

    assert llm.structured_output_calls == []
    assert model == {"procedures": [{"function_id": "F-09", "reason": "", "note": "", "steps": []}]}


# --- 段階6 処理ロジックの詳細 ---


def _logic_output(signature: str = "def create_reservation()") -> LogicGenerationOutput:
    return LogicGenerationOutput(
        signature=signature,
        args="payload: 予約",
        returns="予約",
        raises="なし",
        pre="前",
        post="後",
        pseudo=[GeneratedPseudoStep(text="検証する", sub=["不正なら 422"])],
    )


async def _select_logics(session: AsyncSession, project: Project, logics: list[dict]) -> None:
    """段階6の詳細を書く関数を選んで保存する(画面の「選択を保存」と同じ)。"""
    row = await DesignStageService(session).read(project.id, 6)
    await DesignStageService(session).save(
        project, stage=6, expected_version=row.version, model={"logics": logics}
    )


async def test_stage6_route_generates_pending_logic_and_can_be_approved(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """統合スモーク(段階6): 本文なしで受け付け → 詳細の無い関数を下書き → そのまま承認できる。
    下書きには、その関数を呼ぶ段階5の手順(分岐を含む)とモジュール一覧の行を渡す。"""
    prompts: list[str] = []

    def spy(*args, **kwargs):
        messages = logic_drafting.build_logic_messages(*args, **kwargs)
        prompts.append(str(messages[1].content))
        return messages

    monkeypatch.setattr(generation_service, "build_logic_messages", spy)
    project = await create_stage6_project(db_session)
    project_id = project.id
    await _select_logics(db_session, project, [{"module": ROUTE, "function": "create_reservation"}])
    tasks = BackgroundTasks()
    llm = FakeLLM(structured=_logic_output())

    accepted = await generate_design_stage(6, db_session, project, tasks)
    await DesignStageGenerationService(db_session).execute(
        project_id=project_id, user_id=project.user_id, stage=6, llm=llm
    )
    service = DesignStageService(db_session)
    stage6 = await service.read(project_id, 6)

    assert accepted.generation_status == "generating"
    assert tasks.tasks[0].args[3:] == (None, None, None)
    assert STAGE_GENERATORS[6] is generate_logics
    assert llm.structured_output_calls == [LogicGenerationOutput]
    [prompt] = prompts
    assert "- F-01#1(F-01 " in prompt
    assert "分岐: 本文が不正 → 422" in prompt
    assert "層: api" in prompt
    assert stage6.state == "draft"
    assert stage6.model is not None
    [logic] = stage6.model["logics"]
    assert logic["signature"] == "def create_reservation()"
    assert stage6.issues == []
    approved = await service.approve(project, stage=6, expected_version=stage6.version or 0)
    assert approved.state == "approved"


async def test_stage6_regenerates_only_requested_logic(db_session: AsyncSession) -> None:
    project = await create_stage6_project(db_session)
    project_id = project.id
    human = {"module": ROUTE, "function": "other", "signature": "人の手直し"}
    await _select_logics(
        db_session, project, [{"module": ROUTE, "function": "create_reservation"}, human]
    )
    service = DesignStageGenerationService(db_session)
    await service.request_generation(project, stage=6)
    await service.execute(
        project_id=project_id,
        user_id=project.user_id,
        stage=6,
        llm=FakeLLM(structured=_logic_output("1回目")),
    )

    payload = DesignStageGenerate(logics=[LogicTarget(module=ROUTE, function="create_reservation")])
    tasks = BackgroundTasks()
    await generate_design_stage(6, db_session, project, tasks, payload)
    await service.execute(
        project_id=project_id,
        user_id=project.user_id,
        stage=6,
        logics=[(ROUTE, "create_reservation")],
        llm=FakeLLM(structured=_logic_output("2回目")),
    )
    stage6 = await DesignStageService(db_session).read(project_id, 6)

    assert tasks.tasks[0].args[4] == [(ROUTE, "create_reservation")]
    assert stage6.state == "regenerated"
    assert stage6.model is not None
    target, other = stage6.model["logics"]
    assert target["signature"] == "2回目"
    assert other["signature"] == "人の手直し"


async def test_stage6_rejects_missing_unselected_too_many_and_other_stage(
    db_session: AsyncSession,
) -> None:
    project = await create_stage6_project(db_session)
    service = DesignStageGenerationService(db_session)

    with pytest.raises(DesignStageInvalidError, match="下書きを作る関数がありません"):
        await service.request_generation(project, stage=6)
    await _select_logics(
        db_session, project, [{"module": ROUTE, "function": f"f{n}"} for n in range(6)]
    )
    with pytest.raises(DesignStageInvalidError, match="選ばれていない関数です"):
        await service.request_generation(project, stage=6, logics=[(ROUTE, "missing")])
    with pytest.raises(DesignStageInvalidError, match="5 つまで"):
        await service.request_generation(project, stage=6)
    with pytest.raises(DesignStageInvalidError, match="段階6だけ"):
        await service.request_generation(project, stage=5, logics=[(ROUTE, "f0")])
    accepted = await service.request_generation(project, stage=6, logics=[(ROUTE, "f0")])
    assert accepted.generation_status == "generating"


async def test_stage6_can_be_skipped_by_approving_zero_logics(db_session: AsyncSession) -> None:
    """段階6を飛ばす = 0件を保存して承認する。段階7が開く。"""
    project = await create_stage6_project(db_session)
    service = DesignStageService(db_session)
    await _select_logics(db_session, project, [])
    stage6 = await service.read(project.id, 6)

    approved = await service.approve(project, stage=6, expected_version=stage6.version or 0)
    stage7 = await service.read(project.id, 7)

    assert stage6.issues == []
    assert approved.state == "approved"
    assert stage7.is_open


async def test_generate_logics_skips_unselected_key(db_session: AsyncSession) -> None:
    """選んだ関数に無い鍵は、LLM を呼ばずに飛ばす。"""
    llm = FakeLLM(structured=_logic_output())
    context = StageGenerationContext(
        llm=llm,
        sources=StageSources(),
        previous={"logics": []},
        fingerprint={},
        session=db_session,
        project_id=uuid.uuid4(),
        targets=(f"{ROUTE}::missing",),
    )

    assert await generate_logics(context) == {"logics": []}
    assert llm.structured_output_calls == []
