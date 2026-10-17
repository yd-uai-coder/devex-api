"""生成の失敗の記録(projects.generation_failure)と、理由の分類のテスト。

SUT: `failure_record`(純粋関数)・`classify_failure`(純粋関数)・DocGeneratorService
     (generate / request_generation / recover_if_stale)・ProjectService.get_detail
ドライバ: 各テスト関数
スタブ: FakeLLM(失敗する構造化出力)・StubResolver(キーが無いときの解決の失敗)・FakeRedis
(止まった生成の進み具合)。純粋関数はスタブ不要 ── 例外と値から記録を作るだけで外部を呼ばないため。
DB はインメモリの SQLite(書いた記録そのものが検証対象のため、スタブにしない)。
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.fake_redis import FakeRedis
from tests.fixtures.llm_resolver import stub_llm_resolver

from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.project import ProjectRead
from app.services import llm_retry
from app.services.design_stage_generation_service import _MESSAGES as STAGE_MESSAGES
from app.services.doc_generator_service import DocGeneratorService, failure_record
from app.services.errors import LlmUnavailableError
from app.services.generation_progress import GenerationProgress, GenerationProgressStore
from app.services.project import ProjectService
from app.uml.generation.failures import classify_failure

ALL_SIMPLE = [
    "requirements",
    "external_design",
    "internal_design",
    "implementation_plan",
    "test_spec",
]


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_retry, "RETRY_DELAY_SECONDS", 0)


async def _project(session: AsyncSession, *, status: str = "interviewing") -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="simple", status=status)
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    await session.commit()
    return project


def _failing(exc: Exception) -> FakeLLM:
    failures: list[Any] = [exc] * 3
    return FakeLLM(structured_sequence=failures)


def test_failure_record_has_reason_message_targets_and_time() -> None:
    now = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)

    record = failure_record("GENERATION_FAILED", ("test_spec",), now)

    assert record == {
        "reason_code": "GENERATION_FAILED",
        "message": "設計書の生成に失敗しました。時間をおいて再度お試しください。",
        "targets": ["test_spec"],
        "failed_at": now.isoformat(),
    }


def test_missing_key_is_classified_apart_from_generic_failure() -> None:
    assert classify_failure(LlmUnavailableError("no key")).reason_code == "LLM_UNAVAILABLE"
    assert classify_failure(RuntimeError("down")).reason_code == "GENERATION_FAILED"
    # 段階の下書きの文言も、キーが無いときはマイページを案内する
    assert "マイページ" in STAGE_MESSAGES["LLM_UNAVAILABLE"]


async def test_failed_generation_records_failure_with_all_targets(
    db_session: AsyncSession,
) -> None:
    project = await _project(db_session)

    await DocGeneratorService(db_session).generate(
        project.id, project.user_id, llm=_failing(RuntimeError("down"))
    )

    await db_session.refresh(project)
    failure = project.generation_failure
    assert failure is not None
    assert failure["reason_code"] == "GENERATION_FAILED"
    assert failure["targets"] == ALL_SIMPLE
    # チャットにも同じ文言を残す(今までどおり)
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert history[-1].message == failure["message"]


async def test_quota_failure_records_quota_reason(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _project(db_session)
    # 呼び出しの再試行(invoke_with_retry)が、外部 API の 429 を利用上限として読み替える経路を通す
    monkeypatch.setattr(llm_retry, "_is_quota_error", lambda _exc: True)

    await DocGeneratorService(db_session).generate(
        project.id, project.user_id, llm=_failing(RuntimeError("429"))
    )

    await db_session.refresh(project)
    assert project.generation_failure is not None
    assert project.generation_failure["reason_code"] == "QUOTA_EXCEEDED"
    assert project.generation_failure["message"].startswith("本日の利用上限")


async def test_unresolvable_llm_records_missing_key_reason(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = await _project(db_session)
    stub_llm_resolver(monkeypatch, error=LlmUnavailableError("no key"))

    await DocGeneratorService(db_session).generate(project.id, project.user_id)

    await db_session.refresh(project)
    assert project.status == "interviewing"
    assert project.generation_failure is not None
    assert project.generation_failure["reason_code"] == "LLM_UNAVAILABLE"


async def test_failure_targets_follow_start_and_request_clears_them(
    db_session: AsyncSession,
) -> None:
    project = await _project(db_session, status="revising")
    for doc_type in ALL_SIMPLE[:-1]:
        await GeneratedDocumentRepository(db_session).create_version(
            project_id=project.id, doc_type=doc_type, content=f"# {doc_type}"
        )
    await db_session.commit()
    service = DocGeneratorService(db_session)

    await service.generate(
        project.id,
        project.user_id,
        start_from="test_spec",
        llm=_failing(RuntimeError("down")),
    )
    await db_session.refresh(project)
    assert project.generation_failure is not None
    assert project.generation_failure["targets"] == ["test_spec"]

    await service.request_generation(project)

    assert project.status == "generating"
    assert project.generation_failure is None


async def test_stale_recovery_records_failure_from_progress(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    redis = FakeRedis()
    service = DocGeneratorService(db_session, redis=redis)  # type: ignore[arg-type]
    await service.request_generation(project)
    await GenerationProgressStore(redis).write(  # type: ignore[arg-type]
        project.id, GenerationProgress(targets=["implementation_plan", "test_spec"])
    )
    later = datetime.now(UTC) + timedelta(minutes=16)

    assert await service.recover_if_stale(project, now=later) is True

    failure = project.generation_failure
    assert failure is not None
    assert failure["reason_code"] == "STALE_GENERATION"
    assert failure["targets"] == ["implementation_plan", "test_spec"]
    assert failure["failed_at"] == later.isoformat()


async def test_project_read_and_detail_return_the_failure(db_session: AsyncSession) -> None:
    project = await _project(db_session)
    await DocGeneratorService(db_session).generate(
        project.id, project.user_id, llm=_failing(RuntimeError("down"))
    )
    await db_session.refresh(project)

    detail = await ProjectService(db_session).get_detail(project)
    listed = ProjectRead.model_validate(project)

    assert detail.generation_failure is not None
    assert detail.generation_failure.reason_code == "GENERATION_FAILED"
    assert detail.generation_failure.targets == ALL_SIMPLE
    assert listed.generation_failure == detail.generation_failure
