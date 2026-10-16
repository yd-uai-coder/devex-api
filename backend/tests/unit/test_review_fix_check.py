"""採用した指摘が直ったかの判定(狭い判定のモデル Claude Haiku。シャドーモード)のテスト。

SUT: app/review/fix_check.py(入力の組み立て。純粋関数)・FixCheckService・
     予約札(claim_key・並行の判定の重複を防ぐ)・自分のキーの判定と1日の上限・
     ReviewFixCheckRepository・ReviewService の今の版の判定(gate_summaries の fix_checks)・
     文書の生成の後の判定(DocGeneratorService.generate)・段階の承認のルートの判定の予約・
     E2E の偽物の判定
ドライバ: 各テスト関数
スタブ: 入力の組み立てはスタブ不要 ── 対象が純粋で外部依存を呼ばないため。判定・生成・レビューは
     FakeLLM(構造化出力を返し、プロンプトを記録する)。予約札と1日の回数は FakeRedis。
     DB はインメモリの SQLite。
"""

import uuid
from typing import Any

import pytest
from fastapi import BackgroundTasks
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project, function_list_model
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.fake_redis import FakeRedis
from tests.fixtures.llm_resolver import stub_llm_resolver
from tests.fixtures.review import review_finding, review_output

from app.ai.llm.catalog import Provider, Purpose
from app.ai.llm.fake import E2eFakeLLM
from app.api.routes.design_stages import approve_design_stage
from app.core.config import settings
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.review import ReviewFixCheckRepository, ReviewRunRepository
from app.review import (
    VERDICT_LABEL,
    FixCheckOut,
    Row,
    RowDiff,
    SendbackFinding,
    build_fix_check_messages,
    changed_rows_text,
    target_rows_text,
)
from app.schemas.design_stage import DesignStageApprove
from app.services import llm_retry
from app.services.design_stage_service import DesignStageService
from app.services.doc_generator_service import DocGeneratorService
from app.services.fix_check_service import FixCheckService, claim_key, run_fix_checks
from app.services.review_service import ReviewService


@pytest.fixture(autouse=True)
def _no_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_retry, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(settings, "E2E_FAKE_LLM", False)


# --- 入力の組み立て・判定のモデル ---


def test_messages_put_finding_and_contents() -> None:
    finding = SendbackFinding(
        level="critical",
        cause="design",
        decision="adopted",
        targets=("POST /x",),
        documents=("external_design",),
        problem="認可が無い",
        suggestion="持ち主を確かめる",
    )
    human = str(build_fix_check_messages(finding, "前", "後" * 30000)[1].content)
    assert "対象: POST /x\n問題: 認可が無い\n改善案: 持ち主を確かめる" in human
    assert "## レビューしたときの内容\n前" in human
    # 今の内容は上限の文字数で切る
    assert human.count("後") == 20000
    assert VERDICT_LABEL["partial"] == "一部"


def test_target_rows_text() -> None:
    rows = [Row("FR-001", "要件 FR-001", '{"a":1}'), Row("FR-002", "要件 FR-002", "{}")]
    assert target_rows_text(rows, ["FR-001"]) == '要件 FR-001: {"a":1}'
    assert target_rows_text(rows, ["SCR-1"]) == "(対象の行が無い)"


def test_changed_rows_text_adds_new_and_changed_rows() -> None:
    before = [Row("FR-001", "要件 FR-001", "a"), Row("FR-002", "要件 FR-002", "b")]
    after = [Row("FR-001", "要件 FR-001", "a"), Row("FR-003", "要件 FR-003", "c")]
    text = changed_rows_text(RowDiff(before, after), ["FR-001"])
    # 対象の行はそのまま、足した行は「新しい」、消えた行は名前だけ
    assert text.splitlines() == ["要件 FR-001: a", "要件 FR-003(新しい): c", "要件 FR-002(消えた)"]
    assert changed_rows_text(RowDiff([], []), ["x"]) == "(対象の行が無い)"


async def test_e2e_fake_returns_fix_check() -> None:
    result = await E2eFakeLLM().with_structured_output(FixCheckOut).ainvoke(
        [HumanMessage(content="x")]
    )
    assert result.verdict == "fixed"


# --- 判定の流れ ---


def _generation_llm() -> FakeLLM:
    diagnoses: list[Any] = ["## 自己診断"] * 3
    return FakeLLM(content_sequence=diagnoses, structured_by_schema=upstream_outputs())


def _judge(verdict: str = "partial") -> FakeLLM:
    output = FixCheckOut(verdict=verdict, reason="対象が一部だけ直った")  # type: ignore[arg-type]
    sequence: list[Any] = [output] * 5
    return FakeLLM(structured_sequence=sequence)


async def _adopted_project(session: AsyncSession) -> tuple[Project, User, uuid.UUID]:
    """4文書を作り、外部設計書の全体を指す(行に結べない)採用した指摘を1件持つプロジェクト。"""
    user = User(
        email=f"owner-{uuid.uuid4()}@example.com",
        hashed_password="x",
        is_superuser=True,
        review_enabled=False,
    )
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="simple")
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    await DocGeneratorService(session).generate(project.id, user.id, llm=_generation_llm())
    runs = ReviewRunRepository(session)
    targets = await ReviewService(session).document_targets(project)
    assert targets is not None
    run = await runs.create_run(
        project_id=project.id, gate="documents", targets=targets, trigger="manual", model="m"
    )
    finding = review_finding(problem="画面の全体の整合が無い")
    finding = finding.model_copy(update={"targets": ["画面の全体"]})
    await runs.finish(run, status="succeeded", findings=[finding])
    run.findings[0].decision = "adopted"
    user.review_enabled = True
    await session.commit()
    return project, user, run.findings[0].id


async def test_changed_adopted_finding_is_judged_once_per_version(
    db_session: AsyncSession,
) -> None:
    project, user, finding_id = await _adopted_project(db_session)
    judge = _judge()
    service = FixCheckService(db_session, llm=judge)

    # 版が変わっていない(未対応)の指摘は判定しない
    assert await service.run_for(project, user) == []
    user.review_enabled = False
    await DocGeneratorService(db_session).generate(
        project.id, user.id, start_from="external_design", llm=_generation_llm()
    )
    user.review_enabled = True

    [check] = await service.run_for(project, user)
    assert check.finding_id == finding_id and check.verdict == "partial"
    assert check.targets == {"external_design": 2}
    # 対象を行に結べないときは、レビューの引用と今の文書の本文を渡す
    human = str(judge.structured_messages[0][1].content)
    assert "## レビューしたときの内容\n(レビューの引用)" in human and "# 2. 外部設計書" in human
    # 今の版で判定済みなら、もう一度は判定しない
    assert await service.run_for(project, user) == []

    [summary] = await ReviewService(db_session).gate_summaries(project)
    assert summary.fix_checks[finding_id].verdict == "partial"


def test_claim_key_changes_with_versions() -> None:
    finding_id = uuid.uuid4()
    key = claim_key(finding_id, {"external_design": 2, "requirements": 1})

    assert key.startswith(f"fix_check:{finding_id}:")
    assert key == claim_key(finding_id, {"requirements": 1, "external_design": 2})  # 順は見ない
    assert key != claim_key(finding_id, {"external_design": 3, "requirements": 1})


async def test_claimed_finding_is_skipped_until_released(db_session: AsyncSession) -> None:
    """ほかの処理が (指摘, 版) の札を置いていれば判定しない(並行に走る承認の後の判定の重複)。"""
    project, user, finding_id = await _adopted_project(db_session)
    user.review_enabled = False
    await DocGeneratorService(db_session).generate(
        project.id, user.id, start_from="external_design", llm=_generation_llm()
    )
    user.review_enabled = True
    redis = FakeRedis()
    key = claim_key(finding_id, {"external_design": 2})
    await redis.set(key, "1", nx=True, ex=600)  # 先に走った判定が置いた札
    judge = _judge()

    assert await FixCheckService(db_session, redis=redis, llm=judge).run_for(project, user) == []  # type: ignore[arg-type]
    assert judge.structured_messages == []

    await redis.delete(key)
    [check] = await FixCheckService(db_session, redis=redis, llm=judge).run_for(project, user)  # type: ignore[arg-type]
    assert check.finding_id == finding_id
    assert await redis.get(key) == "1"  # 判定した札は残す(期限まで同じ版を判定させない)


async def test_no_judge_when_review_off_or_without_key(db_session: AsyncSession) -> None:
    project, user, _ = await _adopted_project(db_session)
    user.review_enabled = False
    await DocGeneratorService(db_session).generate(
        project.id, user.id, start_from="external_design", llm=_generation_llm()
    )
    assert await FixCheckService(db_session, llm=_judge()).run_for(project, user) == []
    user.review_enabled = True
    # キーが無ければ(LLM を差し替えなければ)判定しない
    assert await FixCheckService(db_session).run_for(project, user) == []


async def test_judge_with_own_key_skips_daily_limit_and_records_model(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既定のキーの判定だけを1日の上限で数える。記録には解決したモデルを残す。"""
    project, user, finding_id = await _adopted_project(db_session)
    user.review_enabled = False
    await DocGeneratorService(db_session).generate(
        project.id, user.id, start_from="external_design", llm=_generation_llm()
    )
    user.review_enabled = True
    monkeypatch.setattr(settings, "JUDGE_RATE_LIMIT_PER_DAY", 0)
    redis = FakeRedis()
    gemini = {Purpose.JUDGE: (Provider.GEMINI, "gemini-3.5-flash-lite")}

    stub_llm_resolver(monkeypatch, _judge(), models=gemini, own_key=False)
    assert await FixCheckService(db_session, redis=redis).run_for(project, user) == []  # type: ignore[arg-type]
    resolver = stub_llm_resolver(monkeypatch, _judge(), models=gemini, own_key=True)
    [check] = await FixCheckService(db_session, redis=redis).run_for(project, user)  # type: ignore[arg-type]

    assert check.finding_id == finding_id
    assert check.model == "gemini-3.5-flash-lite"
    assert resolver.calls == [(user.id, Purpose.JUDGE)]


async def test_failed_judge_is_recorded(db_session: AsyncSession) -> None:
    project, user, finding_id = await _adopted_project(db_session)
    user.review_enabled = False
    await DocGeneratorService(db_session).generate(
        project.id, user.id, start_from="external_design", llm=_generation_llm()
    )
    user.review_enabled = True
    failures: list[Any] = [RuntimeError("down")] * 5
    failing = FakeLLM(structured_sequence=failures)

    [check] = await FixCheckService(db_session, llm=failing).run_for(project, user)

    assert check.verdict is None and check.error_code == "LLM_API_ERROR"
    latest = await ReviewFixCheckRepository(db_session).latest_for([finding_id])
    assert latest[finding_id].id == check.id


async def test_generation_judges_after_review(db_session: AsyncSession) -> None:
    project, user, finding_id = await _adopted_project(db_session)
    outputs: list[Any] = [review_output()] * 3
    review = FakeLLM(structured_sequence=outputs)

    await DocGeneratorService(db_session).generate(
        project.id,
        user.id,
        start_from="external_design",
        llm=_generation_llm(),
        review_llm=review,
        judge_llm=_judge("fixed"),
    )

    latest = await ReviewFixCheckRepository(db_session).latest_for([finding_id])
    assert latest[finding_id].verdict == "fixed"


async def test_approve_route_schedules_judge_when_review_on(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    owner = await db_session.get(User, project.user_id)
    assert owner is not None
    stages = DesignStageService(db_session)
    saved = await stages.save(project, stage=1, expected_version=None, model=function_list_model())
    tasks = BackgroundTasks()
    owner.is_superuser = owner.review_enabled = True

    await approve_design_stage(
        1, DesignStageApprove(version=saved.version or 0), db_session, project, owner, tasks
    )

    [task] = tasks.tasks
    assert task.func is run_fix_checks and task.args == (project.id, owner.id)
