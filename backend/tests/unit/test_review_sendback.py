"""差し戻し(採用した指摘から再生成の指示を組み立てて、文書の生成に渡す)のテスト。

SUT: app/review/sendback.py(純粋関数)・各文書の生成のプロンプトの組み立て(【修正指示】の節)・
     DocGeneratorService(request_generation / generate の finding_ids)・
     DesignStageGenerationService(段階の生成の finding_ids。修正指示を最後のメッセージに足す)・
     ReviewService.sendback_findings(指摘の確かめ)
ドライバ: 各テスト関数
スタブ: 純粋関数とプロンプトの組み立てはスタブ不要 ── 対象が純粋で外部依存を呼ばないため。
     生成は FakeLLM(4文書・段階1の構造化出力。プロンプトを記録する)。DB はインメモリの SQLite。
"""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.review import review_finding, review_output

from app.detailed_design.drafting import FunctionListGenerationOutput, GeneratedFunction
from app.documents.external_design.drafting import build_external_design_messages
from app.documents.implementation_plan.drafting import build_implementation_plan_messages
from app.documents.internal_design.drafting import build_internal_design_messages
from app.documents.requirements.drafting import build_requirements_messages
from app.documents.revision import NONE_TEXT, REVISION_RULE, instruction_text
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.review import ReviewRunRepository
from app.review import (
    INSTRUCTION_HEAD,
    SendbackFinding,
    build_instruction,
    can_regenerate,
    findings_for,
)
from app.services import llm_retry
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import ReviewFindingNotFoundError, SendbackFindingInvalidError
from app.services.review_service import ReviewService


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_retry, "RETRY_DELAY_SECONDS", 0)


def _finding(**kw: Any) -> SendbackFinding:
    base: dict[str, Any] = {
        "level": "critical",
        "cause": "design",
        "decision": "adopted",
        "targets": ("POST /api/v1/projects",),
        "documents": ("external_design",),
        "problem": "持ち主の確認が無い",
        "suggestion": "持ち主を確かめる",
    }
    return SendbackFinding(**{**base, **kw})


# --- 純粋関数 ---


def test_only_adopted_non_hearing_findings_can_regenerate() -> None:
    assert can_regenerate(_finding())
    assert can_regenerate(_finding(cause="inconsistency"))
    assert not can_regenerate(_finding(cause="hearing"))
    assert not can_regenerate(_finding(decision="held"))


def test_findings_for_documents() -> None:
    external = _finding()
    upstream = _finding(documents=("requirements", "external_design"))
    old = _finding(documents=None)
    findings = [external, upstream, old]
    assert findings_for(["requirements"], findings) == [upstream, old]
    assert findings_for(["internal_design"], findings) == [old]


def test_build_instruction() -> None:
    assert build_instruction([]) == ""
    text = build_instruction([_finding(), _finding(level="minor", targets=())])
    assert text.splitlines() == [
        INSTRUCTION_HEAD,
        "1. [最重要] 対象: POST /api/v1/projects ── 問題: 持ち主の確認が無い"
        " → 改善案: 持ち主を確かめる",
        "2. [軽微] 対象: — ── 問題: 持ち主の確認が無い → 改善案: 持ち主を確かめる",
    ]


def test_builders_put_instruction_section() -> None:
    assert instruction_text("  ") == NONE_TEXT and "【修正指示】" in REVISION_RULE
    messages = [
        build_requirements_messages("会話", {}, None, None, "直す"),
        build_external_design_messages("要件", None, (), "直す"),
        build_internal_design_messages("要件", "外部", None, (), "直す"),
        build_implementation_plan_messages("要件", "内部", None, (), "直す"),
    ]
    for pair in messages:
        assert "## 修正指示\n直す\n\n" in str(pair[1].content)
    assert "## 修正指示\nなし" in str(build_external_design_messages("要件", None)[1].content)


# --- 生成へ渡す ---


async def _reviewed_project(session: AsyncSession) -> tuple[Project, User, list]:
    """4文書を作り、レビューの指摘(review_output の3件)を保存したプロジェクト。"""
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
    await DocGeneratorService(session).generate(project.id, user.id, llm=_llm())
    user.review_enabled = True
    review = ReviewService(session, llm=FakeLLM(structured_sequence=[review_output()]))
    run = await review.start_review(project, user, gate="documents", trigger="manual")
    finished = await review.execute(run.id)
    assert finished is not None
    user.review_enabled = False  # 再生成の後の自動のレビューを流さない
    await session.flush()
    return project, user, list(finished.findings)


def _llm() -> FakeLLM:
    diagnoses: list[Any] = ["## 自己診断"] * 3
    return FakeLLM(content_sequence=diagnoses, structured_by_schema=upstream_outputs())


async def test_request_validates_findings(db_session: AsyncSession) -> None:
    project, _, findings = await _reviewed_project(db_session)
    design = next(f for f in findings if f.cause == "design")
    hearing = next(f for f in findings if f.cause == "hearing")
    service = DocGeneratorService(db_session)

    with pytest.raises(ReviewFindingNotFoundError):
        await service.request_generation(project, finding_ids=[uuid.uuid4()])
    with pytest.raises(SendbackFindingInvalidError):
        await service.request_generation(project, finding_ids=[design.id])  # 未判断
    hearing.decision = "adopted"
    with pytest.raises(SendbackFindingInvalidError):
        await service.request_generation(project, finding_ids=[hearing.id])
    assert project.status != "generating"

    design.decision = "adopted"
    assert await service.request_generation(project, finding_ids=[design.id]) == "completed"


async def test_generate_puts_instruction_into_related_documents(db_session: AsyncSession) -> None:
    project, user, findings = await _reviewed_project(db_session)
    design = next(f for f in findings if f.cause == "design" and f.level == "critical")
    design.decision = "adopted"
    await db_session.flush()
    llm = _llm()

    await DocGeneratorService(db_session).generate(
        project.id, user.id, start_from="requirements", finding_ids=[design.id], llm=llm
    )

    humans = [str(messages[-1].content) for messages in llm.structured_messages]
    # 指摘の関連文書は外部設計書だけ。要件定義書・内部設計書・実装計画書には入れない
    assert "## 修正指示\nなし" in humans[0]
    assert f"問題: {design.problem}" in humans[1] and INSTRUCTION_HEAD in humans[1]
    assert all("## 修正指示\nなし" in h for h in humans[2:])


# --- 段階の生成へ渡す ---


async def _adopted_finding(session: AsyncSession, project: Project, **kw: Any) -> uuid.UUID:
    """プロジェクトに、採用した指摘を1件保存する(レビューの回を経ずに直接)。"""
    runs = ReviewRunRepository(session)
    run = await runs.create_run(
        project_id=project.id, gate="stage6", targets={}, trigger="manual", model="m"
    )
    await runs.finish(run, status="succeeded", findings=[review_finding(**kw)])
    run.findings[0].decision = "adopted"
    await session.flush()
    return run.findings[0].id


async def test_stage_generation_appends_instruction(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session)
    finding_id = await _adopted_finding(
        db_session, project, problem="予約の取消の処理が無い", documents=("detailed_design",)
    )
    output = FunctionListGenerationOutput(
        functions=[
            GeneratedFunction(
                name="予約する", kind="API", trigger="POST /api/v1/reservations",
                screens=["SCR-001"], summary="",
            )
        ]
    )
    llm = FakeLLM(structured_sequence=[output])
    service = DesignStageGenerationService(db_session)

    with pytest.raises(ReviewFindingNotFoundError):
        await service.request_generation(project, stage=1, finding_ids=[uuid.uuid4()])
    await service.request_generation(project, stage=1, finding_ids=[finding_id])
    await service.execute(
        project_id=project.id, user_id=project.user_id, stage=1, finding_ids=[finding_id], llm=llm
    )

    human = str(llm.structured_messages[0][-1].content)
    assert human.endswith("問題: 予約の取消の処理が無い → 改善案: 持ち主の確認を足す")
    assert f"## 修正指示\n{INSTRUCTION_HEAD}" in human
