"""合意した変更要求(CR)を文書の再生成に渡す(反映)のテスト。

SUT: DocGeneratorService.request_generation・generate の`cr_number`
     (app/services/doc_generator_service.py)、ChangeRequestService.reflectable・reflection・
     record_document_apply と純粋関数 merge_applied_marks(app/services/change_request_service.py)、
     DesignStageService.record_document_change の`extra_sources`、marks.cr_source・record、
     リポジトリ create_version(change_request_id)・add_event(refs)とモデルの列、
     スキーマ GenerationRequest・ChangeRequestEventRead、ChangeRequestNotReflectableError、
     ルート trigger_generation(app/api/routes/projects.py)
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)を本物のリポジトリで使う。設計は段階1〜8 を承認済みに
した fixture(`create_impact_project`)から合意済みの版を作る。文書の生成の LLM は FakeLLM
(構造化出力の見本を返し、プロンプトを記録する)
"""

from datetime import UTC, datetime

import pytest
from fastapi import BackgroundTasks
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.requirements import requirements_output

from app.api.routes.projects import trigger_generation
from app.change_requests.instruction import CR_INSTRUCTION_HEAD
from app.detailed_design.impact import Mark, StageImpact
from app.detailed_design.impact.marks import cr_source
from app.models.change_request import ChangeRequestEvent
from app.models.design_stage import DesignStage
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.models.user import User
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.design_stage import DesignStageRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestEventRead,
)
from app.schemas.document import GenerationRequest
from app.services.baseline_service import BaselineService
from app.services.change_request_service import ChangeRequestService, merge_applied_marks
from app.services.design_stage_service import DesignStageService
from app.services.doc_generator_service import DocGeneratorService, generate_documents
from app.services.errors import ChangeRequestNotFoundError, ChangeRequestNotReflectableError

_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
_NEW_TEXT = "予約を登録し、確認メールを送る"


async def _project_with_cr(session: AsyncSession, *, agree: bool = True) -> Project:
    """段階1〜8 承認済みの設計・合意済みの版 B-1・FR-001 を対象にする CR-1(既定で合意)。"""
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
                "targets": [
                    {"requirement_id": "FR-001", "kind": "content", "proposed_text": _NEW_TEXT}
                ],
            }
        ),
        created_by=None,
    )
    if agree:
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


def _llm() -> FakeLLM:
    return FakeLLM(
        content_sequence=["## 自己診断\n最重要: 特になし", "## 自己診断\n最重要: 特になし"],
        structured_by_schema=upstream_outputs(
            requirements_output(prev_id="FR-001", text=_NEW_TEXT)
        ),
    )


async def _generate(session: AsyncSession, project: Project, llm: FakeLLM) -> None:
    status = await DocGeneratorService(session).request_generation(
        project, start_from="requirements", cr_number=1
    )
    await DocGeneratorService(session).generate(
        project.id,
        project.user_id,
        status_before_generation=status,
        start_from="requirements",
        cr_number=1,
        llm=llm,
    )


async def test_generate_with_cr_puts_instruction_and_records_apply(
    db_session: AsyncSession,
) -> None:
    project = await _project_with_cr(db_session)
    llm = _llm()

    await _generate(db_session, project, llm)

    # 作り直した設計書のどちらにも同じ CR の指示が入る
    humans = [str(messages[-1].content) for messages in llm.structured_messages]
    head = CR_INSTRUCTION_HEAD.format(number=1)
    assert all(head in h for h in humans[:2])
    assert f"- FR-001(内容の変更): 変更後の案「{_NEW_TEXT}」" in humans[0]

    # 版に CR とモデル名が残る
    view = await ChangeRequestService(db_session).get(project, 1)
    cr = view.change_request
    documents = GeneratedDocumentRepository(db_session)
    for doc_type in ("requirements", "external_design"):
        current = await documents.get_current(project_id=project.id, doc_type=doc_type)
        assert isinstance(current, GeneratedDocument)
        assert current.change_request_id == cr.id
        assert current.generated_model is not None

    # 反映済みになり、時系列に作った版とモデル名が残る
    assert cr.status == "applied"
    event = ChangeRequestEventRead.model_validate(cr.events[-1])
    assert (event.action, event.from_status, event.to_status) == ("apply", "agreed", "applied")
    assert event.refs is not None
    assert [d["doc_type"] for d in event.refs["documents"]] == ["requirements", "external_design"]
    assert "要件定義書 v2" in event.note

    # 付いた印は出どころに cr:1 を持ち、CR に写しが残る
    stage1 = await DesignStageRepository(db_session).get(project_id=project.id, stage=1)
    assert isinstance(stage1, DesignStage)
    marks = StageImpact.from_dict(stage1.impact).marks
    f01 = next(m for m in marks if m.key == "fn:F-01")
    assert {"doc:requirements", cr_source(1)} <= set(f01.sources)
    assert cr.applied_impact is not None
    assert "fn:F-01" in {m["key"] for m in cr.applied_impact}


async def test_reapply_adds_a_row_and_keeps_applied(db_session: AsyncSession) -> None:
    project = await _project_with_cr(db_session)
    await _generate(db_session, project, _llm())

    await _generate(db_session, project, _llm())

    cr = await ChangeRequestRepository(db_session).get_by_number(project_id=project.id, number=1)
    assert cr is not None
    applies = [e for e in cr.events if e.action == "apply"]
    assert all(isinstance(e, ChangeRequestEvent) and e.refs is not None for e in applies)
    assert [(e.from_status, e.to_status) for e in applies] == [
        ("agreed", "applied"),
        ("applied", "applied"),
    ]


async def test_request_validates_the_change_request(db_session: AsyncSession) -> None:
    project = await _project_with_cr(db_session, agree=False)
    service = DocGeneratorService(db_session)

    with pytest.raises(ChangeRequestNotFoundError):
        await service.request_generation(project, start_from="requirements", cr_number=9)
    with pytest.raises(ChangeRequestNotReflectableError):  # 下書きはまだ反映できない
        await service.request_generation(project, start_from="requirements", cr_number=1)
    assert project.status != "generating"


async def test_request_rejects_test_spec_only(db_session: AsyncSession) -> None:
    project = await _project_with_cr(db_session)

    with pytest.raises(ChangeRequestNotReflectableError):
        await DocGeneratorService(db_session).request_generation(
            project, start_from="test_spec", cr_number=1
        )


async def test_stage_reflection_requires_applied(db_session: AsyncSession) -> None:
    project = await _project_with_cr(db_session)
    service = ChangeRequestService(db_session)

    assert (await service.reflectable(project, 1)).status == "agreed"
    with pytest.raises(ChangeRequestNotReflectableError):
        await service.reflectable(project, 1, stage=True)


async def test_background_skips_a_change_request_that_is_no_longer_reflectable(
    db_session: AsyncSession,
) -> None:
    project = await _project_with_cr(db_session)
    status = await DocGeneratorService(db_session).request_generation(
        project, start_from="requirements", cr_number=1
    )
    cr = (await ChangeRequestService(db_session).get(project, 1)).change_request
    cr.status = "verified"  # 受け付けの後に状態が変わった
    await db_session.commit()
    llm = _llm()

    await DocGeneratorService(db_session).generate(
        project.id,
        project.user_id,
        status_before_generation=status,
        start_from="requirements",
        cr_number=1,
        llm=llm,
    )

    assert all("変更要求" not in str(m[-1].content) for m in llm.structured_messages)
    current = await GeneratedDocumentRepository(db_session).get_current(
        project_id=project.id, doc_type="requirements"
    )
    assert current is not None and current.change_request_id is None
    assert [e.action for e in cr.events][-1] == "agree"
    assert await ChangeRequestService(db_session).reflection(project.id, None) is None


async def test_record_document_change_returns_nothing_without_models(
    db_session: AsyncSession,
) -> None:
    project = await _project_with_cr(db_session)

    marks = await DesignStageService(db_session).record_document_change(
        project, "requirements", None, {}, extra_sources=(cr_source(1),)
    )

    assert marks == []


def test_merge_applied_marks_overlays_by_key() -> None:
    old = Mark("fn:F-01", "登録", 1, ("req:FR-001", "fn:F-01"), "旧", ("doc:requirements",))
    new = Mark("fn:F-01", "登録", 1, ("req:FR-001", "fn:F-01"), "新", ("doc:requirements",))
    unit = Mark("unit:U-002", "U-002", 7, ("req:FR-001", "unit:U-002"), "単位", ("cr:1",))

    merged = merge_applied_marks(merge_applied_marks(None, [unit, old]), [new])

    assert [(m["key"], m["reason"]) for m in merged] == [("fn:F-01", "新"), ("unit:U-002", "単位")]


def test_generation_request_cr_number_is_positive() -> None:
    assert GenerationRequest.model_validate({"cr_number": 2}).cr_number == 2
    assert GenerationRequest.model_validate({}).cr_number is None
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate({"cr_number": 0})


async def test_route_passes_cr_number_to_the_background_task(db_session: AsyncSession) -> None:
    project = await _project_with_cr(db_session)
    tasks = BackgroundTasks()
    user = await db_session.get(User, project.user_id)
    assert user is not None

    await trigger_generation(
        db_session,
        project,
        user,
        tasks,
        GenerationRequest(start_from="requirements", cr_number=1),
    )

    [task] = tasks.tasks
    assert task.func is generate_documents
    assert task.kwargs["cr_number"] == 1
