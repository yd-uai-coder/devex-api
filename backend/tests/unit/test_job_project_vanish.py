"""バックグラウンドの処理の、プロジェクトの消失への強さのテスト(生成中でも削除できるため)。

SUT: DocGeneratorService.generate・ReviewService.execute・FixCheckService.run_for・
     DesignStageGenerationService.execute と、共通の部品 commit_if_live
ドライバ: 各テスト関数
スタブ: VanishingLLM(最初の LLM の呼び出しの前にプロジェクトに削除の印を付ける。`fail` で失敗の
経路も通す)と、その中の FakeLLM。DB はインメモリの SQLite(結果・失敗の記録が残らないことそのものが
検証対象のため、スタブにしない)。外部キーの違反は SQLite では起きないので、commit_if_live の
テストで例外を直接投げて確かめる。
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.review import review_finding, review_output
from tests.fixtures.vanishing_llm import VanishingLLM

from app.core.config import settings
from app.detailed_design.drafting import FunctionListGenerationOutput, GeneratedFunction
from app.models.design_stage import DesignStage
from app.models.project import Project
from app.models.review import ReviewFixCheck
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.project import ProjectRepository
from app.repositories.review import ReviewRunRepository
from app.review import DOCUMENTS_GATE, FixCheckOut
from app.services import llm_retry
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.doc_generator_service import DocGeneratorService
from app.services.fix_check_service import FixCheckService
from app.services.project_deletion import commit_if_live
from app.services.review_service import ReviewService


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_retry, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", False)


def _generation_llm() -> FakeLLM:
    diagnoses: list[Any] = ["## 自己診断"] * 3
    return FakeLLM(content_sequence=diagnoses, structured_by_schema=upstream_outputs())


async def _project(session: AsyncSession, *, review: bool = False) -> tuple[Project, User]:
    user = User(
        email=f"owner-{uuid.uuid4()}@example.com",
        hashed_password="x",
        is_superuser=True,
        review_enabled=review,
    )
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="simple")
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    await session.commit()
    return project, user


async def _status(session: AsyncSession, project_id: uuid.UUID) -> str | None:
    return await session.scalar(select(Project.status).where(Project.id == project_id))


async def _notices(session: AsyncSession, project_id: uuid.UUID) -> list[str]:
    history = await ChatHistoryRepository(session).list_for_project(project_id)
    return [h.message for h in history if h.sender == "others"]


# --- 共通の部品 ---


async def test_commit_if_live_commits_only_live_projects(db_session: AsyncSession) -> None:
    project, _ = await _project(db_session)
    project_id = project.id
    project.title = "書き換え"

    assert await commit_if_live(db_session, project_id, job="test") is True

    await ProjectRepository(db_session).mark_deleted(project)
    await db_session.commit()
    project.title = "捨てる"
    assert await commit_if_live(db_session, project_id, job="test") is False
    db_session.expunge_all()
    assert await db_session.scalar(select(Project.title).where(Project.id == project_id)) == (
        "書き換え"
    )


async def test_commit_if_live_reads_vanished_errors(db_session: AsyncSession) -> None:
    project, _ = await _project(db_session)
    error = IntegrityError("INSERT", {}, Exception("foreign key"))

    async def _write_after_purge() -> None:
        # 書き込みの途中で物理削除された(外部キーの違反)。印で消えたことを模す
        await ProjectRepository(db_session).mark_deleted(project)
        await db_session.commit()
        raise error

    async def _write_failing() -> None:
        raise error

    assert (
        await commit_if_live(db_session, project.id, job="test", write=_write_after_purge) is False
    )
    other, _ = await _project(db_session)
    with pytest.raises(IntegrityError):  # 生きているプロジェクトの違反は、そのまま上げる
        await commit_if_live(db_session, other.id, job="test", write=_write_failing)


# --- 文書の生成 ---


async def test_documents_deleted_mid_run_are_discarded(db_session: AsyncSession) -> None:
    project, user = await _project(db_session)
    project_id = project.id
    llm = VanishingLLM(_generation_llm(), db_session, project_id)

    await DocGeneratorService(db_session).generate(project_id, user.id, llm=llm)

    assert llm.marked
    assert await GeneratedDocumentRepository(db_session).list_current_for_project(project_id) == []
    assert await _status(db_session, project_id) == "generating"  # completed にしない
    assert await _notices(db_session, project_id) == []  # 自己診断も載せない


async def test_failed_generation_of_deleted_project_is_not_recorded(
    db_session: AsyncSession,
) -> None:
    project, user = await _project(db_session)
    project_id = project.id
    llm = VanishingLLM(_generation_llm(), db_session, project_id, fail=RuntimeError("down"))

    await DocGeneratorService(db_session).generate(project_id, user.id, llm=llm)

    assert await _status(db_session, project_id) == "generating"  # 状態を戻さない
    assert await _notices(db_session, project_id) == []  # 失敗の通知を書かない


async def test_generation_of_marked_project_does_nothing(db_session: AsyncSession) -> None:
    project, user = await _project(db_session)
    await ProjectRepository(db_session).mark_deleted(project)
    await db_session.commit()
    llm = _generation_llm()

    await DocGeneratorService(db_session).generate(project.id, user.id, llm=llm)

    assert llm.structured_output_calls == []


# --- 設計レビュー ---


async def _review_run(session: AsyncSession) -> tuple[Project, uuid.UUID]:
    project, user = await _project(session)
    await DocGeneratorService(session).generate(project.id, user.id, llm=_generation_llm())
    user.review_enabled = True
    await session.flush()
    run = await ReviewService(session, llm=FakeLLM()).start_review(
        project, user, gate=DOCUMENTS_GATE, trigger="manual"
    )
    return project, run.id


@pytest.mark.parametrize("fail", [None, RuntimeError("down")])
async def test_review_of_deleted_project_records_nothing(
    db_session: AsyncSession, fail: Exception | None
) -> None:
    project, run_id = await _review_run(db_session)
    project_id = project.id
    before = await _notices(db_session, project_id)
    outputs: list[Any] = [review_output()] * 3
    inner = FakeLLM(structured_sequence=outputs)
    llm = VanishingLLM(inner, db_session, project_id, fail=fail)

    finished = await ReviewService(db_session, llm=llm).execute(run_id)

    assert finished is None
    db_session.expunge_all()
    run = await ReviewRunRepository(db_session).find_one(id=run_id)
    assert run is not None and run.status == "running"  # 成功も失敗も記録しない
    assert await _notices(db_session, project_id) == before


# --- 「直ったか」の判定 ---


async def test_fix_check_of_deleted_project_records_nothing(db_session: AsyncSession) -> None:
    project, user = await _project(db_session)
    generator = DocGeneratorService(db_session)
    await generator.generate(project.id, user.id, llm=_generation_llm())
    runs = ReviewRunRepository(db_session)
    targets = await ReviewService(db_session).document_targets(project)
    assert targets is not None
    run = await runs.create_run(
        project_id=project.id, gate="documents", targets=targets, trigger="manual", model="m"
    )
    finding = review_finding(problem="画面の全体の整合が無い")
    await runs.finish(
        run, status="succeeded", findings=[finding.model_copy(update={"targets": ["画面の全体"]})]
    )
    run.findings[0].decision = "adopted"
    await generator.generate(
        project.id, user.id, start_from="external_design", llm=_generation_llm()
    )
    user.review_enabled = True
    await db_session.commit()
    verdicts: list[Any] = [FixCheckOut(verdict="fixed", reason="直った")] * 3
    judge = FakeLLM(structured_sequence=verdicts)
    llm = VanishingLLM(judge, db_session, project.id)

    checks = await FixCheckService(db_session, llm=llm).run_for(project, user)

    assert llm.marked and checks == []
    assert (await db_session.scalars(select(ReviewFixCheck))).all() == []


# --- 段階の生成 ---


@pytest.mark.parametrize("fail", [None, RuntimeError("down")])
async def test_stage_generation_of_deleted_project_records_nothing(
    db_session: AsyncSession, fail: Exception | None
) -> None:
    project = await create_detailed_project(db_session)
    project_id = project.id
    await db_session.commit()
    service = DesignStageGenerationService(db_session)
    await service.request_generation(project, stage=1)
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
    llm = VanishingLLM(FakeLLM(structured=output), db_session, project_id, fail=fail)

    await service.execute(project_id=project_id, user_id=project.user_id, stage=1, llm=llm)

    db_session.expunge_all()
    row = await db_session.scalar(
        select(DesignStage).where(DesignStage.project_id == project_id, DesignStage.stage == 1)
    )
    assert row is not None
    assert row.generation_status == "generating"  # completed にも failed にもしない
    assert row.model is None
