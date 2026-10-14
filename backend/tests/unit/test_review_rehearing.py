"""差し戻しの聞き直し(採用したヒアリングの指摘で、観点を未決に戻してチャットで聞き直す)のテスト。

SUT: app/review/rehearing.py(純粋関数)・merge_judgement の未決に戻した観点の規則
     (app/hearing/points.py)・ReviewService.rehearing と ChatService.reopen_points / judge_points・
     HearingPointRepository(戻した印の保存)・ルート request_rehearing
ドライバ: 各テスト関数
スタブ: 純粋関数はスタブ不要 ── 対象が純粋で外部依存を呼ばないため。観点の判定は FakeLLM
     (判定の構造化出力を返す)。DB はインメモリの SQLite。
"""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.review import review_finding

from app.api.routes.reviews import request_rehearing
from app.hearing import JudgedPoint, PointState, initial_states, merge_judgement
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.hearing_point import HearingPointRepository
from app.repositories.review import ReviewRunRepository
from app.review import (
    REHEARING_HEAD,
    SendbackFinding,
    can_rehear,
    rehearing_message,
    rehearing_points,
)
from app.schemas.hearing import HearingPointsJudgement, JudgedPointOut
from app.schemas.review import RehearingRequest
from app.services.chat_service import ChatService
from app.services.errors import (
    DocGenerationInProgressError,
    RehearingFindingInvalidError,
    ReviewFindingNotFoundError,
)
from app.services.review_service import ReviewService


def _finding(**kw: Any) -> SendbackFinding:
    base: dict[str, Any] = {
        "level": "major",
        "cause": "hearing",
        "decision": "adopted",
        "targets": ("NFR-001",),
        "documents": ("requirements",),
        "problem": "誰が誰のデータを見られるかが決まっていない",
        "suggestion": "アクセス制御を決める",
        "point_key": "security",
    }
    return SendbackFinding(**{**base, **kw})


# --- 純粋関数 ---


def test_only_adopted_hearing_findings_with_point_can_rehear() -> None:
    assert can_rehear(_finding())
    assert not can_rehear(_finding(cause="design"))
    assert not can_rehear(_finding(decision="held"))
    assert not can_rehear(_finding(point_key=None))


def test_rehearing_points_follow_points_table_order() -> None:
    abuse = _finding(point_key="abuse", problem="不正利用の対策が無い")
    security = _finding()
    points = rehearing_points([abuse, security, _finding(cause="design")])
    assert list(points) == ["security", "abuse"]


def test_rehearing_message() -> None:
    lines = rehearing_message(rehearing_points([_finding()])).splitlines()
    assert lines[0] == REHEARING_HEAD
    assert lines[1] == "・「セキュリティ」"
    assert lines[2] == "  - 誰が誰のデータを見られるかが決まっていない"
    assert lines[3] == "    改善案: アクセス制御を決める"
    assert lines[4].startswith("  例えば: 入れたデータを、誰に見られたら困りますか")


def test_merge_keeps_reopened_point_open_until_answered_after_reopen() -> None:
    previous = initial_states()
    previous["security"] = PointState("security", "open", "", reopened_after="ai-9")
    old = [JudgedPoint("security", "decided", "ログインだけ", ("m1",))]
    new = [JudgedPoint("security", "decided", "本人だけが見られる", ("m1", "m5"))]

    # 聞き直しより前の発言だけを根拠にした決定は取り込まない
    kept = merge_judgement(
        previous, old, user_messages={"m1", "m5"}, after_reopen={"security": {"m5"}}
    )
    assert kept["security"] == previous["security"]
    # 未決のままなら印を引き継ぐ
    still = merge_judgement(
        previous, [JudgedPoint("security", "open", "", ())], user_messages=set()
    )
    assert still["security"].reopened_after == "ai-9"
    # 聞き直しの後の答えがあれば決まり、印は外れる
    decided = merge_judgement(
        previous, new, user_messages={"m1", "m5"}, after_reopen={"security": {"m5"}}
    )
    assert decided["security"].status == "decided" and decided["security"].reopened_after is None


# --- 聞き直しの流れ ---


async def _project(session: AsyncSession) -> tuple[Project, list]:
    """観点 security を決めた、完成したプロジェクトと、レビューの指摘(ヒアリング・設計)。"""
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="simple", status="completed")
    session.add(project)
    await session.flush()
    answer = await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="ログインして使う"
    )
    states = initial_states()
    states["security"] = PointState("security", "decided", "ログインする", (str(answer.id),))
    await HearingPointRepository(session).save_states(project.id, states.values())
    runs = ReviewRunRepository(session)
    run = await runs.create_run(
        project_id=project.id, gate="documents", targets={}, trigger="manual", model="m"
    )
    await runs.finish(
        run,
        status="succeeded",
        findings=[
            review_finding(cause="hearing", point_key="security", problem="アクセス制御が無い"),
            review_finding(cause="design", problem="認可が無い"),
        ],
    )
    for f in run.findings:
        f.decision = "adopted"
    await session.flush()
    return project, sorted(run.findings, key=lambda f: f.cause)


def _judge(evidence: list[int]) -> FakeLLM:
    judgement = HearingPointsJudgement(
        points=[
            JudgedPointOut(
                key="security", status="decided", summary="本人だけが見られる", evidence=evidence
            )
        ]
    )
    return FakeLLM(structured_by_schema={HearingPointsJudgement: judgement})


async def test_rehearing_reopens_point_and_asks_in_chat(db_session: AsyncSession) -> None:
    project, (design, hearing) = await _project(db_session)

    view = await ReviewService(db_session).rehearing(project, [hearing.id])

    states = await HearingPointRepository(db_session).load_states(project.id)
    assert states["security"].status == "open" and states["security"].reopened_after is not None
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert history[-1].sender == "ai" and history[-1].message.startswith(REHEARING_HEAD)
    assert "アクセス制御が無い" in history[-1].message
    assert str(history[-1].id) == states["security"].reopened_after
    assert project.status == "revising"
    assert project.hearing_check is not None and project.hearing_check["is_sufficient"] is False
    assert any(p.key == "security" and p.status == "open" for p in view.points)

    with pytest.raises(RehearingFindingInvalidError):
        await ReviewService(db_session).rehearing(project, [design.id])
    with pytest.raises(ReviewFindingNotFoundError):
        await ReviewService(db_session).rehearing(project, [uuid.uuid4()])
    project.status = "generating"
    with pytest.raises(DocGenerationInProgressError):
        await ReviewService(db_session).rehearing(project, [hearing.id])


async def test_judge_needs_answer_after_rehearing(db_session: AsyncSession) -> None:
    project, (_, hearing) = await _project(db_session)
    await ReviewService(db_session).rehearing(project, [hearing.id])
    chat = ChatService(db_session)

    # 聞き直しより前の発言(#1)だけを根拠にした決定は取り込まない
    states = await chat.judge_points(project, llm=_judge([1]))
    assert states["security"].status == "open"

    await ChatHistoryRepository(db_session).add(
        project_id=project.id, sender="user", message="本人だけが自分のデータを見られる"
    )
    states = await chat.judge_points(project, llm=_judge([2]))
    assert states["security"].status == "decided" and states["security"].reopened_after is None


async def test_route_requests_rehearing(db_session: AsyncSession) -> None:
    project, (_, hearing) = await _project(db_session)
    view = await request_rehearing(RehearingRequest(finding_ids=[hearing.id]), db_session, project)
    assert any(p.key == "security" and p.status == "open" for p in view.points)
