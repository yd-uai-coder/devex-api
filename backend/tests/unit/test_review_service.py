"""設計レビューのサービス(ReviewService)と、文書の生成の後の自動のレビュー
(DocGeneratorService.generate)のテスト。段階6・7の区切り・区切りの状態・指摘の採否も見る。

SUT は ReviewService(受け付け・実行・止まった回の回収)と DocGeneratorService.generate、
ドライバは pytest(`db_session` fixture)。スタブは FakeLLM(生成の Gemini とレビューの Claude を
それぞれ偽物にする)と FakeRedis(1日の回数を数える)。DB はインメモリの SQLite を実際に使う。
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import (
    create_detailed_project,
    create_document_project,
    create_stage7_project,
    logic_model,
)
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.fake_redis import FakeRedis
from tests.fixtures.review import review_finding, review_output

from app.core.config import settings
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.review import ReviewRunRepository
from app.review import DOCUMENTS_GATE, ReviewOutput
from app.services import llm_retry
from app.services.design_stage_service import DesignStageService
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import (
    DocGenerationInProgressError,
    GenerationFailedError,
    ReviewDocumentsNotReadyError,
    ReviewFindingNotFoundError,
    ReviewInProgressError,
    ReviewNotAllowedError,
    ReviewRateLimitExceededError,
    ReviewUnavailableError,
)
from app.services.review_service import ReviewService

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


# --- 受け付け(start_review) ---


async def test_start_rejects_non_superuser_and_setting_off(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session, superuser=False)
    with pytest.raises(ReviewNotAllowedError):
        await ReviewService(db_session).start_review(
            project, user, gate=DOCUMENTS_GATE, trigger="manual"
        )

    project, user = await _generated(db_session, enabled=False)
    with pytest.raises(ReviewNotAllowedError):
        await ReviewService(db_session).start_review(
            project, user, gate=DOCUMENTS_GATE, trigger="manual"
        )


async def test_start_requires_documents_and_no_generation(db_session: AsyncSession) -> None:
    project, user = await _project(db_session)
    service = ReviewService(db_session, llm=_review_llm())
    with pytest.raises(ReviewDocumentsNotReadyError):
        await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")

    project.status = "generating"
    with pytest.raises(DocGenerationInProgressError):
        await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")


async def test_start_without_key_is_unavailable(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session)

    with pytest.raises(ReviewUnavailableError):
        await ReviewService(db_session).start_review(
            project, user, gate=DOCUMENTS_GATE, trigger="manual"
        )


async def test_start_creates_running_run_for_current_versions(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session, mode="detailed")
    service = ReviewService(db_session, llm=_review_llm())

    run = await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")

    assert run.status == "running" and run.gate == DOCUMENTS_GATE
    # 詳細設計モードは要件・外部だけ
    assert run.targets == {"requirements": 1, "external_design": 1}
    with pytest.raises(ReviewInProgressError):
        await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")


async def test_start_counts_daily_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "REVIEW_RATE_LIMIT_PER_DAY", 1)
    project, user = await _generated(db_session)
    service = ReviewService(db_session, redis=FakeRedis(), llm=_review_llm())  # type: ignore[arg-type]
    run = await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")
    await service.execute(run.id)

    with pytest.raises(ReviewRateLimitExceededError):
        await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")


async def test_stale_running_run_is_recovered(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session)
    service = ReviewService(db_session, llm=_review_llm())
    stuck = await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")
    stuck.started_at = datetime.now(UTC) - timedelta(minutes=16)
    await db_session.flush()

    run = await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")

    assert stuck.status == "failed" and stuck.error_code == "STALE_REVIEW"
    assert run.status == "running"


# --- 実行(execute) ---


async def test_execute_saves_findings_and_posts_to_chat(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session)
    review_llm = _review_llm()
    service = ReviewService(db_session, llm=review_llm)
    run = await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")

    finished = await service.execute(run.id)

    assert finished is not None and finished.status == "succeeded"
    assert [f.level for f in finished.findings] == ["critical", "major", "minor"]
    assert review_llm.structured_output_methods == ["json_schema"]
    human = str(review_llm.structured_messages[0][1].content)
    assert "## 実装計画書" in human and "# ヒアリングの観点の表" in human
    assert (await _messages(db_session, project))[-1] == (
        "設計レビュー(文書の生成の後)をしました。指摘 3件(最重要 1・中程度 1・軽微 1)。"
        "レビューのパネルで確かめてください。"
    )
    latest = await service.latest(project, DOCUMENTS_GATE)
    assert latest is not None and latest.id == run.id


async def test_execute_records_failure_without_raising(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session)
    service = ReviewService(db_session, llm=_review_llm(RuntimeError("down")))
    run = await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")

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
    assert len(messages) == 1 and messages[0].startswith("設計レビュー(文書の生成の後)をしました")
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


# --- 段階6・7の区切り・区切りの状態・採否 ---


async def _reviewer(session: AsyncSession, project: Project) -> User:
    """段階の fixture のプロジェクトの持ち主を、レビューを使える superuser にする。"""
    user = await session.get(User, project.user_id)
    assert user is not None
    user.is_superuser = True
    user.review_enabled = True
    await session.flush()
    return user


async def test_stage_gates_are_not_ready_on_simple_or_unapproved(db_session: AsyncSession) -> None:
    simple, simple_user = await _generated(db_session)
    with pytest.raises(ReviewDocumentsNotReadyError):
        await ReviewService(db_session, llm=_review_llm()).start_review(
            simple, simple_user, gate="stage6", trigger="manual"
        )
    project = await create_stage7_project(db_session)
    user = await _reviewer(db_session, project)
    with pytest.raises(ReviewDocumentsNotReadyError, match="段階1〜7"):
        await ReviewService(db_session, llm=_review_llm()).start_review(
            project, user, gate="stage7", trigger="manual"
        )


async def test_stage6_gate_reviews_design_document_with_upstream(db_session: AsyncSession) -> None:
    project = await create_stage7_project(db_session)
    user = await _reviewer(db_session, project)
    # 詳細設計モードに無い内部設計書を挙げても、レビューした文書だけを残す
    output = ReviewOutput(
        findings=[review_finding(documents=("internal_design", "detailed_design"))]
    )
    review_llm = _review_llm(output)
    service = ReviewService(db_session, llm=review_llm)

    run = await service.start_review(project, user, gate="stage6", trigger="manual")
    finished = await service.execute(run.id)

    assert run.targets == {
        "requirements": 1,
        "external_design": 1,
        **{f"stage:{stage}": 1 for stage in range(1, 7)},
    }
    assert finished is not None and finished.status == "succeeded"
    human = str(review_llm.structured_messages[0][1].content)
    assert "## 要件定義書" in human and "## 外部設計書" in human
    assert "## 詳細設計書\n# 詳細設計書: p" in human and "06 処理ロジックの詳細" in human
    assert "07 横断事項" not in human and "## 実装計画書(詳細設計)" not in human
    assert finished.findings[0].documents == ["detailed_design"]
    assert (await _messages(db_session, project))[-1].startswith(
        "設計レビュー(段階6の承認の後)をしました。指摘 1件"
    )


async def test_stage7_gate_adds_plan(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)
    user = await _reviewer(db_session, project)
    review_llm = _review_llm()
    service = ReviewService(db_session, llm=review_llm)

    run = await service.start_review(project, user, gate="stage7", trigger="manual")
    await service.execute(run.id)

    assert run.targets["stage:7"] == 1
    human = str(review_llm.structured_messages[0][1].content)
    assert "07 横断事項" in human and "## 実装計画書(詳細設計)\n# 実装計画書: p" in human


async def test_gate_summaries_follow_versions(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)
    user = await _reviewer(db_session, project)
    service = ReviewService(db_session, llm=_review_llm())
    run = await service.start_review(project, user, gate="stage6", trigger="manual")

    running = {s.gate: s.status for s in await service.gate_summaries(project)}
    assert running == {"documents": "unreviewed", "stage6": "running", "stage7": "unreviewed"}

    await service.execute(run.id)
    summaries = {s.gate: s for s in await service.gate_summaries(project)}
    assert summaries["stage6"].status == "reviewed" and summaries["stage6"].is_current
    assert summaries["stage6"].run is not None and len(summaries["stage6"].run.findings) == 3

    # 段階6を直して承認し直すと、段階6の区切りは未レビューに戻る(段階7は古くなり、そろわない)
    stages = DesignStageService(db_session)
    await stages.save(project, stage=6, expected_version=1, model=logic_model())
    await stages.approve(project, stage=6, expected_version=2)
    after = {s.gate: s for s in await service.gate_summaries(project)}
    assert after["stage6"].status == "unreviewed" and not after["stage6"].is_current
    assert after["stage7"].status == "not_ready"


async def test_gate_summaries_of_simple_and_not_ready(db_session: AsyncSession) -> None:
    service = ReviewService(db_session)
    project, _ = await _project(db_session)
    simple = [(s.gate, s.status) for s in await service.gate_summaries(project)]
    assert simple == [("documents", "not_ready")]
    detailed = await create_detailed_project(db_session)
    statuses = [(s.gate, s.status) for s in await service.gate_summaries(detailed)]
    assert statuses == [
        ("documents", "unreviewed"),
        ("stage6", "not_ready"),
        ("stage7", "not_ready"),
    ]


async def test_decide_records_decision_of_own_project(db_session: AsyncSession) -> None:
    project, user = await _generated(db_session)
    service = ReviewService(db_session, llm=_review_llm())
    run = await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")
    finished = await service.execute(run.id)
    assert finished is not None
    finding = finished.findings[0]
    assert finding.decision == "pending"

    decided = await service.decide(project, finding.id, "adopted")

    assert decided.decision == "adopted"
    other, _ = await _project(db_session)
    with pytest.raises(ReviewFindingNotFoundError):
        await service.decide(other, finding.id, "rejected")
    with pytest.raises(ReviewFindingNotFoundError):
        await service.decide(project, uuid.uuid4(), "rejected")


# --- 採用した指摘の追跡 ---


async def test_followups_of_adopted_findings_follow_document_versions(
    db_session: AsyncSession,
) -> None:
    project, user = await _generated(db_session)
    service = ReviewService(db_session, llm=_review_llm())
    run = await service.start_review(project, user, gate=DOCUMENTS_GATE, trigger="manual")
    finished = await service.execute(run.id)
    assert finished is not None and finished.snapshots is None
    adopted, pending = finished.findings[0], finished.findings[1]
    await service.decide(project, adopted.id, "adopted")

    summary = (await service.gate_summaries(project))[0]
    # 採用した指摘だけ。関連文書の版が変わっていないので未対応
    assert summary.followups == {adopted.id: "unaddressed"}
    assert await service.followup_of(project, adopted) == "unaddressed"
    assert await service.followup_of(project, pending) is None

    # 外部設計以降を作り直すと、版が変わる(対象の行を比べる段へ進む)
    await DocGeneratorService(db_session).generate(
        project.id, user.id, start_from="external_design", llm=_generation_llm()
    )
    # 作り直しの後の自動のレビューで最新の回が替わっても、前の回で採用した指摘として残る
    after = (await service.gate_summaries(project))[0]
    assert after.run is not None and after.run.id != finished.id
    assert [f.id for f in after.adopted_before] == [adopted.id]
    assert after.followups[adopted.id] in ("target_unchanged", "changed")
    assert (await service.current_versions(project))["external_design"] == 2


async def test_stage_gate_keeps_snapshots_for_followups(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)
    user = await _reviewer(db_session, project)
    output = ReviewOutput(findings=[review_finding(documents=("detailed_design",))])
    service = ReviewService(db_session, llm=_review_llm(output))
    run = await service.start_review(project, user, gate="stage6", trigger="manual")
    finished = await service.execute(run.id)
    assert finished is not None and finished.snapshots is not None
    assert sorted(finished.snapshots) == [f"stage:{s}" for s in range(1, 7)]
    finding = finished.findings[0]
    await service.decide(project, finding.id, "adopted")
    assert await service.followup_of(project, finding) == "unaddressed"

    # 段階6を直すと(承認の前でも)段階の版が変わり、写しと今の内容を比べる
    await DesignStageService(db_session).save(
        project, stage=6, expected_version=1, model=logic_model()
    )
    elements = await DesignStageService(db_session).elements_since(
        project, {6: finished.snapshots["stage:6"]}
    )
    assert set(elements) == {6}
    assert await service.followup_of(project, finding) in ("target_unchanged", "changed")
