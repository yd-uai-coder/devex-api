"""設計レビューのサービス(ReviewService)と、文書の生成の後の自動のレビュー
(DocGeneratorService.generate)のテスト。

SUT は ReviewService(受け付け・実行・止まった回の回収)と DocGeneratorService.generate、
ドライバは pytest(`db_session` fixture)。スタブは FakeLLM(生成の Gemini とレビューの Claude を
それぞれ偽物にする)と FakeRedis(1日の回数を数える)。DB はインメモリの SQLite を実際に使う。
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.fake_redis import FakeRedis
from tests.fixtures.review import review_output

from app.core.config import settings
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.review import ReviewRunRepository
from app.review import ReviewOutput
from app.services import llm_retry
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import (
    DocGenerationInProgressError,
    GenerationFailedError,
    ReviewDocumentsNotReadyError,
    ReviewInProgressError,
    ReviewNotAllowedError,
    ReviewRateLimitExceededError,
    ReviewUnavailableError,
)
from app.services.review_service import DOCUMENTS_GATE, ReviewService

_DIAGNOSIS = "## 自己診断\n最重要: 特になし"


@pytest.fixture(autouse=True)
def _no_review_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # 失敗の再試行を待たない
    monkeypatch.setattr(llm_retry, "RETRY_DELAY_SECONDS", 0)
    # 開発機の環境変数のキーで実 API を呼ばないようにする(キーが要るテストは LLM を差し替える)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", False)


async def _project(
    session: AsyncSession, *, superuser: bool = True, enabled: bool = True, mode: str = "simple"
) -> tuple[Project, User]:
    user = User(
        email=f"owner-{uuid.uuid4()}@example.com",
        hashed_password="x",
        is_superuser=superuser,
        review_enabled=enabled,
    )
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode=mode)
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    return project, user


def _generation_llm(*, diagnoses: int = 1) -> FakeLLM:
    # 4文書は構造化出力、自己診断だけが ainvoke
    sequence: list[Any] = [_DIAGNOSIS] * diagnoses
    return FakeLLM(content_sequence=sequence, structured_by_schema=upstream_outputs())


def _review_llm(output: ReviewOutput | Exception | None = None) -> FakeLLM:
    item = output if output is not None else review_output()
    sequence: list[Any] = [item] * 3
    return FakeLLM(structured_sequence=sequence)


async def _messages(session: AsyncSession, project: Project) -> list[str]:
    history = await ChatHistoryRepository(session).list_for_project(project.id)
    return [h.message for h in history if h.sender == "others"]


async def _generated(session: AsyncSession, **kwargs) -> tuple[Project, User]:
    """レビューがオフの状態で文書を作ってから、設定を戻す(受け付け・実行のテスト用)。"""
    enabled = kwargs.pop("enabled", True)
    project, user = await _project(session, enabled=False, **kwargs)
    await DocGeneratorService(session).generate(project.id, user.id, llm=_generation_llm())
    user.review_enabled = enabled
    await session.flush()
    return project, user


# --- 受け付け(start_documents_review) ---


async def test_start_rejects_non_superuser_and_setting_off(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session, superuser=False)
    with pytest.raises(ReviewNotAllowedError):
        await ReviewService(db_session).start_documents_review(project, user, trigger="manual")

    project, user = await _generated(db_session, enabled=False)
    with pytest.raises(ReviewNotAllowedError):
        await ReviewService(db_session).start_documents_review(project, user, trigger="manual")


async def test_start_requires_documents_and_no_generation(db_session: AsyncSession) -> None:
    project, user = await _project(db_session)
    service = ReviewService(db_session, llm=_review_llm())
    with pytest.raises(ReviewDocumentsNotReadyError):
        await service.start_documents_review(project, user, trigger="manual")

    project.status = "generating"
    with pytest.raises(DocGenerationInProgressError):
        await service.start_documents_review(project, user, trigger="manual")


async def test_start_without_key_is_unavailable(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session)

    with pytest.raises(ReviewUnavailableError):
        await ReviewService(db_session).start_documents_review(project, user, trigger="manual")


async def test_start_creates_running_run_for_current_versions(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session, mode="detailed")
    service = ReviewService(db_session, llm=_review_llm())

    run = await service.start_documents_review(project, user, trigger="manual")

    assert run.status == "running" and run.gate == DOCUMENTS_GATE
    # 詳細設計モードは要件・外部だけ
    assert run.targets == {"requirements": 1, "external_design": 1}
    with pytest.raises(ReviewInProgressError):
        await service.start_documents_review(project, user, trigger="manual")


async def test_start_counts_daily_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REVIEW_RATE_LIMIT_PER_DAY", 1)
    project, user = await _generated(db_session)
    service = ReviewService(db_session, redis=FakeRedis(), llm=_review_llm())  # type: ignore[arg-type]
    run = await service.start_documents_review(project, user, trigger="manual")
    await service.execute(run.id)

    with pytest.raises(ReviewRateLimitExceededError):
        await service.start_documents_review(project, user, trigger="manual")


async def test_stale_running_run_is_recovered(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session)
    service = ReviewService(db_session, llm=_review_llm())
    stuck = await service.start_documents_review(project, user, trigger="manual")
    stuck.started_at = datetime.now(UTC) - timedelta(minutes=16)
    await db_session.flush()

    run = await service.start_documents_review(project, user, trigger="manual")

    assert stuck.status == "failed" and stuck.error_code == "STALE_REVIEW"
    assert run.status == "running"


# --- 実行(execute) ---


async def test_execute_saves_findings_and_posts_to_chat(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session)
    review_llm = _review_llm()
    service = ReviewService(db_session, llm=review_llm)
    run = await service.start_documents_review(project, user, trigger="manual")

    finished = await service.execute(run.id)

    assert finished is not None and finished.status == "succeeded"
    assert [f.level for f in finished.findings] == ["critical", "major", "minor"]
    assert review_llm.structured_output_methods == ["json_schema"]
    human = str(review_llm.structured_messages[0][1].content)
    assert "## 実装計画書" in human and "# ヒアリングの観点の表" in human
    assert (await _messages(db_session, project))[-1].startswith("## 設計レビュー\n指摘 3件")
    latest = await service.latest_documents_review(project)
    assert latest is not None and latest.id == run.id


async def test_execute_records_failure_without_raising(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session)
    service = ReviewService(db_session, llm=_review_llm(RuntimeError("down")))
    run = await service.start_documents_review(project, user, trigger="manual")

    finished = await service.execute(run.id)

    assert finished is not None and finished.status == "failed"
    # invoke_with_retry が規定回数の後に GenerationFailedError に包む
    assert finished.error_code == GenerationFailedError.code
    assert (await _messages(db_session, project))[-1].startswith("設計レビューに失敗しました")


# --- 生成の後の自動のレビュー(DocGeneratorService.generate) ---


async def test_generation_with_review_off_keeps_self_diagnosis(db_session: AsyncSession) -> None:
    project, user = await _project(db_session, enabled=False)
    review_llm = _review_llm()

    await DocGeneratorService(db_session).generate(
        project.id, user.id, llm=_generation_llm(), review_llm=review_llm
    )

    assert await _messages(db_session, project) == [_DIAGNOSIS]
    assert review_llm.structured_output_calls == []
    assert await ReviewRunRepository(db_session).count() == 0


async def test_generation_with_review_on_reviews_after_completion(db_session: AsyncSession) -> None:
    project, user = await _project(db_session)

    await DocGeneratorService(db_session).generate(
        project.id, user.id, llm=_generation_llm(diagnoses=0), review_llm=_review_llm()
    )

    assert project.status == "completed"
    messages = await _messages(db_session, project)
    assert len(messages) == 1 and messages[0].startswith("## 設計レビュー")
    run = await ReviewRunRepository(db_session).get_latest(project_id=project.id, gate="documents")
    assert run is not None and run.trigger == "auto" and run.status == "succeeded"


async def test_generation_falls_back_to_self_diagnosis_when_review_fails(
    db_session: AsyncSession,
) -> None:
    project, user = await _project(db_session)

    await DocGeneratorService(db_session).generate(
        project.id, user.id, llm=_generation_llm(), review_llm=_review_llm(RuntimeError("down"))
    )

    assert project.status == "completed"
    messages = await _messages(db_session, project)
    assert messages[-2].startswith("設計レビューに失敗しました")
    assert messages[-1] == _DIAGNOSIS


async def test_generation_without_key_records_failed_run_and_self_diagnoses(
    db_session: AsyncSession,
) -> None:
    project, user = await _project(db_session)

    await DocGeneratorService(db_session).generate(project.id, user.id, llm=_generation_llm())

    run = await ReviewRunRepository(db_session).get_latest(project_id=project.id, gate="documents")
    assert run is not None and run.status == "failed"
    assert run.error_code == "REVIEW_UNAVAILABLE"
    assert run.targets["requirements"] == 1
    messages = await _messages(db_session, project)
    assert messages[-2].startswith("設計レビューを流せませんでした")
    assert messages[-1] == _DIAGNOSIS
