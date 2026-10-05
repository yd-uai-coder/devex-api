"""設計書の生成の二重実行防止・失敗時の rollback・止まった生成の回収のテスト。

SUT: DocGeneratorService(request_generation / generate / recover_if_stale)と、それを呼ぶルート・
     ProjectService.get_detail
ドライバ: 各テスト関数
スタブ: FakeLLM ── 2件目の呼び出しで応答が尽きて例外になる(途中で失敗する生成を模す)。
DBはインメモリSQLite(rollbackで版が残らないことそのものが検証対象のため、スタブにしない)。
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.uml import create_project

from app.api.routes.projects import trigger_generation
from app.models.generated_document import GeneratedDocument
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.services import llm_retry
from app.services.doc_generator_service import DocGeneratorService, generate_documents
from app.services.errors import DocGenerationInProgressError
from app.services.project import ProjectService

LATER = datetime.now(UTC) + timedelta(minutes=16)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm_retry.asyncio, "sleep", _instant_sleep)


async def test_trigger_generation_route_marks_generating_and_queues_previous_status(
    db_session: AsyncSession,
) -> None:
    project = await create_project(db_session)
    user = await db_session.get(User, project.user_id)
    assert user is not None
    tasks = BackgroundTasks()

    await trigger_generation(db_session, project, user, tasks)

    assert project.status == "generating"
    [task] = tasks.tasks
    assert task.func is generate_documents
    assert task.args == (project.id, user.id, "interviewing")


async def test_request_while_generating_is_conflict(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = DocGeneratorService(db_session)
    await service.request_generation(project)

    with pytest.raises(DocGenerationInProgressError):
        await service.request_generation(project)


async def test_failure_midway_rolls_back_partial_versions(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = DocGeneratorService(db_session)
    previous = await service.request_generation(project)

    # 要件定義だけ生成でき、外部設計で応答が尽きて失敗する
    await service.generate(
        project.id,
        project.user_id,
        status_before_generation=previous,
        llm=FakeLLM(content_sequence=["# 要件"]),
    )

    documents = await GeneratedDocumentRepository(db_session).list_current_for_project(project.id)
    assert documents == []
    await db_session.refresh(project)
    assert project.status == "interviewing"
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert history[-1].message == "設計書の生成に失敗しました。時間をおいて再度お試しください。"


async def test_stale_generating_is_recovered_before_new_request(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = DocGeneratorService(db_session)
    await service.request_generation(project)
    await db_session.refresh(project)

    recovered = await service.recover_if_stale(project, now=LATER)

    assert recovered is True
    assert project.status == "interviewing"
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert "中断しました" in history[-1].message


async def test_recent_generating_is_not_recovered(db_session: AsyncSession) -> None:
    project = await create_project(db_session)
    service = DocGeneratorService(db_session)
    await service.request_generation(project)
    await db_session.refresh(project)

    assert await service.recover_if_stale(project) is False
    assert project.status == "generating"


async def test_project_detail_recovers_stale_generation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await create_project(db_session)
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="requirements", content="# 要件"
    )
    await DocGeneratorService(db_session).request_generation(project)
    project.updated_at = datetime.now(UTC) - timedelta(minutes=30)
    await db_session.commit()

    detail = await ProjectService(db_session).get_detail(project)

    # 文書が既にあるので、再生成できる revising に戻す
    assert detail.status == "revising"


async def test_duplicate_document_version_is_rejected_by_unique_constraint(
    db_session: AsyncSession,
) -> None:
    """409と画面の無効化をすり抜けた並行実行でも、同じ版の番号はDBで止まる。"""
    project = await create_project(db_session)
    for _ in range(2):
        db_session.add(
            GeneratedDocument(
                project_id=project.id, doc_type="requirements", content="x", version=1
            )
        )

    with pytest.raises(IntegrityError):
        await db_session.flush()
