"""設計書の再生成の起点(その文書から下流を作り直す)と、生成の進み具合のテスト。

SUT: `generation_targets`(純粋関数)・DocGeneratorService(request_generation / generate)・
     GenerationProgressStore と、ルート(trigger_generation / get_generation_progress)
ドライバ: 各テスト関数
スタブ: FakeLLM(4文書の構造化出力と自己診断)・FakeRedis(進み具合の書き込みを記録する)。
DB はインメモリの SQLite(作った版・作らなかった版そのものが検証対象のため、スタブにしない)。
"""

import json
import uuid
from typing import Any

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.fake_redis import FakeRedis

from app.api.routes.projects import get_generation_progress, trigger_generation
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.document import GenerationRequest
from app.services import llm_retry
from app.services.doc_generator_service import (
    DocGeneratorService,
    generate_documents,
    generation_targets,
)
from app.services.errors import GenerationStartInvalidError, GenerationUpstreamMissingError
from app.services.generation_progress import GenerationProgress, GenerationProgressStore


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_retry, "RETRY_DELAY_SECONDS", 0)


class RecordingRedis(FakeRedis):
    """進み具合の書き込みを順に記録する FakeRedis。"""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[dict[str, Any]] = []

    async def set(self, key: str, value: str, *, ex: int | None = None) -> bool:
        self.writes.append(json.loads(value))
        return await super().set(key, value, ex=ex)


def _llm() -> FakeLLM:
    # 4文書は構造化出力、自己診断だけが ainvoke
    diagnoses: list[Any] = ["## 自己診断"] * 3
    return FakeLLM(content_sequence=diagnoses, structured_by_schema=upstream_outputs())


async def _simple_project(session: AsyncSession) -> tuple[Project, User]:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="simple")
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    return project, user


async def _versions(session: AsyncSession, project: Project) -> dict[str, int]:
    documents = await GeneratedDocumentRepository(session).list_current_for_project(project.id)
    return {d.doc_type: d.version for d in documents}


def test_generation_targets_are_start_and_downstream() -> None:
    assert generation_targets("simple", None) == (
        "requirements",
        "external_design",
        "internal_design",
        "implementation_plan",
    )
    assert generation_targets("simple", "internal_design") == (
        "internal_design",
        "implementation_plan",
    )
    assert generation_targets("simple", "implementation_plan") == ("implementation_plan",)
    assert generation_targets("detailed", "external_design") == ("external_design",)
    with pytest.raises(GenerationStartInvalidError):
        generation_targets("detailed", "internal_design")


async def test_regenerate_from_internal_design_keeps_upstream(db_session: AsyncSession) -> None:
    project, user = await _simple_project(db_session)
    await DocGeneratorService(db_session).generate(project.id, user.id, llm=_llm())
    kept = await GeneratedDocumentRepository(db_session).get_current(
        project_id=project.id, doc_type="requirements"
    )
    assert kept is not None
    llm = _llm()

    await DocGeneratorService(db_session).generate(
        project.id, user.id, start_from="internal_design", llm=llm
    )

    assert await _versions(db_session, project) == {
        "requirements": 1,
        "external_design": 1,
        "internal_design": 2,
        "implementation_plan": 2,
    }
    # 作り直したのは内部設計書・実装計画書の2回だけで、上流は表示中の版の本文を入力にした
    assert len(llm.structured_messages) == 2
    assert kept.content in str(llm.structured_messages[0][-1].content)
    assert project.status == "completed"


async def test_request_rejects_missing_upstream_and_invalid_start(
    db_session: AsyncSession,
) -> None:
    project, _ = await _simple_project(db_session)
    service = DocGeneratorService(db_session)
    with pytest.raises(GenerationUpstreamMissingError):
        await service.request_generation(project, start_from="external_design")
    assert project.status != "generating"

    detailed = await create_detailed_project(db_session)
    with pytest.raises(GenerationStartInvalidError):
        await service.request_generation(detailed, start_from="internal_design")
    # 上流(要件定義書)があれば外部設計書から受け付ける
    assert await service.request_generation(detailed, start_from="external_design") == "completed"


async def test_progress_is_written_per_document_and_cleared(db_session: AsyncSession) -> None:
    project, user = await _simple_project(db_session)
    await DocGeneratorService(db_session).generate(project.id, user.id, llm=_llm())
    redis = RecordingRedis()

    await DocGeneratorService(db_session, redis=redis).generate(  # type: ignore[arg-type]
        project.id, user.id, start_from="external_design", llm=_llm()
    )

    targets = ["external_design", "internal_design", "implementation_plan"]
    assert redis.writes[0] == {"targets": targets, "done": [], "current": None}
    assert redis.writes[1] == {"targets": targets, "done": [], "current": "external_design"}
    assert redis.writes[2] == {
        "targets": targets,
        "done": ["external_design"],
        "current": "internal_design",
    }
    assert redis.writes[-1] == {"targets": targets, "done": targets, "current": None}
    # 終わったら消す
    assert await GenerationProgressStore(redis).read(project.id) is None  # type: ignore[arg-type]


async def test_progress_is_cleared_on_failure(db_session: AsyncSession) -> None:
    project, user = await _simple_project(db_session)
    redis = RecordingRedis()
    failures: list[Any] = [RuntimeError("down")] * 3
    failing = FakeLLM(structured_sequence=failures)

    await DocGeneratorService(db_session, redis=redis).generate(  # type: ignore[arg-type]
        project.id, user.id, llm=failing
    )

    assert redis.writes[-1]["current"] == "requirements"
    assert await GenerationProgressStore(redis).read(project.id) is None  # type: ignore[arg-type]
    assert await _versions(db_session, project) == {}


async def test_routes_pass_start_and_read_progress(db_session: AsyncSession) -> None:
    project, user = await _simple_project(db_session)
    await DocGeneratorService(db_session).generate(project.id, user.id, llm=_llm())
    tasks = BackgroundTasks()

    await trigger_generation(
        db_session, project, user, tasks, GenerationRequest(start_from="implementation_plan")
    )

    [task] = tasks.tasks
    assert task.func is generate_documents
    assert task.kwargs == {"start_from": "implementation_plan"}

    redis = FakeRedis()
    assert await get_generation_progress(redis, project) is None  # type: ignore[arg-type]
    await GenerationProgressStore(redis).write(  # type: ignore[arg-type]
        project.id,
        GenerationProgress(targets=["implementation_plan"], current="implementation_plan"),
    )
    read = await get_generation_progress(redis, project)  # type: ignore[arg-type]
    assert read is not None and read.current == "implementation_plan" and read.done == []
