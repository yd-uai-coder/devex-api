"""UmlGenerationService のテスト。

SUT: UmlGenerationService(request_generation / execute_run / list_candidates / list_runs)
ドライバ: 各テスト関数(サービスのメソッドを直接呼ぶ)
スタブ: FakeLLM(tests/fixtures/fake_llm.py)── 構造化出力(Gemini)の代わり。
DBはインメモリSQLite(db_session)で、スタブにはしない(上書き・履歴の保存そのものが検証対象のため)。
"""

import uuid

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.uml import (
    INTERNAL_DESIGN_MD,
    component_output,
    create_project,
    create_project_with_internal_design,
    dfd_output,
    er_output,
)

from app.repositories.data_item import DataItemRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services import llm_retry
from app.services.errors import (
    ErScopeRequiredError,
    TooManySubjectsError,
    UmlGenerationInProgressError,
    UmlSourceDocumentMissingError,
    UmlSubjectNotFoundError,
)
from app.services.uml_generation_service import (
    MAX_SUBJECTS_PER_REQUEST,
    SubjectRequest,
    UmlGenerationService,
)
from app.uml.generation import ComponentGenerationOutput, DfdGenerationOutput
from app.uml.validation.structural import MAX_ELEMENTS

DF1 = "POST /api/v1/reservations"
DF2 = "GET /api/v1/reservations"
DF3 = "予約リマインドバッチ"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """リトライ間隔(1秒)をテストで待たないようにする。"""

    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_retry.asyncio, "sleep", _instant_sleep)


async def _generate(
    session: AsyncSession,
    project_id: uuid.UUID,
    notation,
    subjects: list[SubjectRequest],
    llm: FakeLLM,
):
    service = UmlGenerationService(session)
    run = await service.request_generation(
        project_id=project_id, notation=notation, subjects=subjects
    )
    await service.execute_run(project_id=project_id, run_id=run.id, llm=llm)
    await session.refresh(run)
    return run


async def test_list_candidates_parses_internal_design(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)

    candidates = await UmlGenerationService(db_session).list_candidates(project.id)

    assert candidates.internal_design_version == 1
    assert [s.title for s in candidates.dfd_subjects] == [DF1, DF2, DF3]
    assert candidates.er_tables == ["users", "reservations"]


async def test_list_candidates_is_empty_without_internal_design(db_session: AsyncSession) -> None:
    project = await create_project(db_session)

    candidates = await UmlGenerationService(db_session).list_candidates(project.id)

    assert candidates.internal_design_version is None
    assert candidates.dfd_subjects == [] and candidates.er_tables == []


async def test_request_requires_internal_design(db_session: AsyncSession) -> None:
    project = await create_project(db_session)

    with pytest.raises(UmlSourceDocumentMissingError):
        await UmlGenerationService(db_session).request_generation(
            project_id=project.id, notation="component", subjects=[]
        )


async def test_request_marks_new_diagram_generating_and_creates_run(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)

    run = await UmlGenerationService(db_session).request_generation(
        project_id=project.id, notation="component", subjects=[]
    )

    diagram = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project.id, notation="component", subject=""
    )
    assert diagram is not None
    assert diagram.generation_status == "generating"
    assert run.status == "running"
    assert run.requested == [{"subject": "", "diagram_id": str(diagram.id)}]


async def test_request_rejects_while_another_generation_is_running(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    service = UmlGenerationService(db_session)
    await service.request_generation(project_id=project.id, notation="component", subjects=[])

    with pytest.raises(UmlGenerationInProgressError):
        await service.request_generation(
            project_id=project.id, notation="dfd", subjects=[SubjectRequest(DF1)]
        )


async def test_request_rejects_unknown_dfd_subject_and_empty_dfd_request(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    service = UmlGenerationService(db_session)

    with pytest.raises(UmlSubjectNotFoundError):
        await service.request_generation(
            project_id=project.id, notation="dfd", subjects=[SubjectRequest("DELETE /unknown")]
        )
    with pytest.raises(UmlSubjectNotFoundError):
        await service.request_generation(project_id=project.id, notation="dfd", subjects=[])


async def test_request_rejects_too_many_subjects(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    subjects = [SubjectRequest(f"s{i}") for i in range(MAX_SUBJECTS_PER_REQUEST + 1)]

    with pytest.raises(TooManySubjectsError):
        await UmlGenerationService(db_session).request_generation(
            project_id=project.id, notation="dfd", subjects=subjects
        )


async def test_request_requires_er_scope_when_tables_exceed_limit(
    db_session: AsyncSession,
) -> None:
    many_tables = "\n\n".join(f"### テーブル: t{i}\n| id |" for i in range(MAX_ELEMENTS + 1))
    content = f"# 3. 内部設計書\n\n## 3.2 データモデル定義\n\n{many_tables}\n"
    project = await create_project_with_internal_design(db_session, content)
    service = UmlGenerationService(db_session)

    with pytest.raises(ErScopeRequiredError):
        await service.request_generation(project_id=project.id, notation="er", subjects=[])
    # 部分図としてテーブルを選べば受け付ける
    run = await service.request_generation(
        project_id=project.id,
        notation="er",
        subjects=[SubjectRequest("予約まわり", tables=["t0", "t1"])],
    )
    assert run.status == "running"


async def test_request_rejects_er_partial_without_tables_or_with_unknown_table(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    service = UmlGenerationService(db_session)

    with pytest.raises(ErScopeRequiredError):
        await service.request_generation(
            project_id=project.id, notation="er", subjects=[SubjectRequest("予約まわり")]
        )
    with pytest.raises(UmlSubjectNotFoundError):
        await service.request_generation(
            project_id=project.id,
            notation="er",
            subjects=[SubjectRequest("予約まわり", tables=["no_such_table"])],
        )


async def test_component_generation_fills_diagram_and_records_success(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    llm = FakeLLM(structured_sequence=[component_output()])

    run = await _generate(db_session, project.id, "component", [], llm)

    diagram = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project.id, notation="component", subject=""
    )
    assert diagram is not None
    assert diagram.generation_status == "completed"
    assert diagram.generation_error is None
    assert diagram.version == 2
    assert diagram.source_doc_versions == {"internal_design": 1}
    # Phase 9の申し送り: AI生成がlayerを埋める
    assert [e["layer"] for e in diagram.semantic_model["elements"]] == ["api", "service"]
    assert llm.structured_output_calls == [ComponentGenerationOutput]
    assert run.status == "completed"
    assert run.results[0]["outcome"] == "succeeded"
    assert run.finished_at is not None


async def test_regeneration_overwrites_same_diagram_and_resets_layout(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    await _generate(db_session, project.id, "er", [], FakeLLM(structured_sequence=[er_output()]))
    repo = UmlDiagramRepository(db_session)
    first = await repo.get_by_subject(project_id=project.id, notation="er", subject="")
    assert first is not None
    first.layout_model = {"dummy": True}
    first.status = "approved"
    await db_session.commit()

    await _generate(db_session, project.id, "er", [], FakeLLM(structured_sequence=[er_output()]))

    diagrams = await repo.list_by_notation(project.id, "er")
    assert [d.id for d in diagrams] == [first.id]
    await db_session.refresh(first)
    assert first.layout_model is None
    assert first.status == "draft"
    assert first.version == 3


async def test_er_partial_diagram_saves_scope_and_reuses_it_on_regeneration(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    llm = FakeLLM(structured_sequence=[er_output(), er_output()])
    await _generate(
        db_session, project.id, "er", [SubjectRequest("予約", tables=["reservations"])], llm
    )

    await _generate(db_session, project.id, "er", [SubjectRequest("予約")], llm)

    diagram = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project.id, notation="er", subject="予約"
    )
    assert diagram is not None and diagram.scope == {"tables": ["reservations"]}


async def test_dfd_generation_creates_new_data_items_and_reuses_existing(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    data_items = DataItemRepository(db_session)
    existing = await data_items.create(
        project_id=project.id, name="予約リクエスト", fields=[{"name": "編集済み"}]
    )
    await db_session.commit()
    output = dfd_output()
    output.data_items.append(output.data_items[0].model_copy(update={"name": "予約結果"}))
    llm = FakeLLM(structured_sequence=[output])

    run = await _generate(db_session, project.id, "dfd", [SubjectRequest(DF1)], llm)

    assert run.status == "completed"
    items = {item.name: item for item in await data_items.list_for_project(project.id)}
    assert set(items) == {"予約リクエスト", "予約結果"}
    # 既存の項目はユーザーの編集を上書きしない
    assert items["予約リクエスト"].id == existing.id
    assert items["予約リクエスト"].fields == [{"name": "編集済み"}]
    diagram = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project.id, notation="dfd", subject=DF1
    )
    assert diagram is not None
    assert {f["data_item_id"] for f in diagram.semantic_model["relations"]} == {str(existing.id)}
    assert llm.structured_output_calls == [DfdGenerationOutput]


async def test_quota_exceeded_stops_and_skips_remaining_subjects(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_project_with_internal_design(db_session)
    # 失敗時のrollbackで同じセッションのORMオブジェクトが失効するため、idは値として持つ
    project_id = project.id
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)
    llm = FakeLLM(structured_sequence=[dfd_output(), RuntimeError("429")])

    run = await _generate(
        db_session,
        project_id,
        "dfd",
        [SubjectRequest(DF1), SubjectRequest(DF2), SubjectRequest(DF3)],
        llm,
    )

    assert run.status == "partial"
    assert [(r["subject"], r["outcome"], r["reason_code"]) for r in run.results] == [
        (DF1, "succeeded", None),
        (DF2, "failed", "QUOTA_EXCEEDED"),
        (DF3, "skipped", "QUOTA_EXCEEDED"),
    ]
    assert all("再度生成を指示してください" in r["message"] for r in run.results[1:])
    # 3件目はLLMを呼んでいない(構造化出力の呼び出しは2回だけ)
    assert len(llm.structured_output_calls) == 2
    skipped = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project_id, notation="dfd", subject=DF3
    )
    assert skipped is not None and skipped.generation_status == "failed"


async def test_token_limit_is_recorded_without_retry(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    # 失敗時のrollbackで同じセッションのORMオブジェクトが失効するため、idは値として持つ
    project_id = project.id
    truncated = {
        "raw": AIMessage(content="", response_metadata={"finish_reason": "MAX_TOKENS"}),
        "parsed": None,
        "parsing_error": ValueError("truncated"),
    }
    llm = FakeLLM(structured_sequence=[truncated])

    run = await _generate(db_session, project_id, "component", [], llm)

    assert run.status == "failed"
    assert run.results[0]["reason_code"] == "TOKEN_LIMIT"
    assert len(llm.structured_output_calls) == 1


async def test_invalid_output_after_retries_keeps_previous_model(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    # 失敗時のrollbackで同じセッションのORMオブジェクトが失効するため、idは値として持つ
    project_id = project.id
    await _generate(
        db_session, project_id, "component", [], FakeLLM(structured_sequence=[component_output()])
    )
    broken = {
        "raw": AIMessage(content="", response_metadata={"finish_reason": "STOP"}),
        "parsed": None,
        "parsing_error": ValueError("broken"),
    }
    llm = FakeLLM(structured_sequence=[broken, broken, broken])

    run = await _generate(db_session, project_id, "component", [], llm)

    assert run.results[0]["reason_code"] == "INVALID_OUTPUT"
    diagram = await UmlDiagramRepository(db_session).get_by_subject(
        project_id=project_id, notation="component", subject=""
    )
    assert diagram is not None
    assert diagram.generation_status == "failed"
    assert diagram.generation_error == run.results[0]["message"]
    # 前回の生成結果は残す
    assert len(diagram.semantic_model["elements"]) == 2


async def test_list_runs_returns_history(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    await _generate(
        db_session, project.id, "component", [], FakeLLM(structured_sequence=[component_output()])
    )

    runs = await UmlGenerationService(db_session).list_runs(project.id)

    assert len(runs) == 1 and runs[0].notation == "component"


def test_fixture_internal_design_has_three_dfd_subjects() -> None:
    assert INTERNAL_DESIGN_MD.count("#### DF-") == 3
