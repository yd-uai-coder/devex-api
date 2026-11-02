"""変更要求(CR)に設計レビューの回を結ぶテスト。

SUT: 純粋関数 first_applied_at・review_link_problem・aware(app/change_requests/reviews.py)、
     ChangeRequestService.review_candidates・link_review と verify の時系列の参照
     (app/services/change_request_service.py)、ChangeRequestRepository.link_review、
     ReviewRunRepository.list_for_project、モデル ChangeRequestReview、スキーマ
     ReviewRunBriefRead・LinkedReviewRead・ChangeRequestReviewLink、ReviewRunNotFoundError・
     ChangeRequestReviewNotLinkableError、ルート list_change_request_review_candidates・
     link_change_request_review(app/api/routes/change_requests.py)
ドライバ: 各テスト関数
スタブ: 純粋関数のテストはスタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため。
サービスとルートは、インメモリ SQLite(`db_session`)と tests/fixtures/change_requests.py(反映まで)。
レビューの回は LLM を呼ばず、リポジトリで直接作る
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_requests import (
    agreed_change_request_project,
    applied_change_request_project,
)

from app.api.routes.change_requests import (
    check_change_request_documents,
    get_change_request_verification,
    link_change_request_review,
    list_change_request_review_candidates,
    verify_change_request,
)
from app.change_requests import aware, first_applied_at, review_link_problem
from app.models.change_request import ChangeRequestReview
from app.models.project import Project
from app.models.review import ReviewFinding, ReviewRun
from app.models.user import User
from app.repositories.review import ReviewRunRepository
from app.schemas.change_request import (
    ChangeRequestReviewLink,
    ChangeRequestVerify,
    DocumentChecksUpdate,
    LinkedReviewRead,
    ReviewRunBriefRead,
)
from app.services.design_stage_service import DesignStageService
from app.services.errors import ChangeRequestReviewNotLinkableError, ReviewRunNotFoundError

_T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def test_first_applied_at_and_aware() -> None:
    events = [("create", _T0), ("apply", _T0.replace(tzinfo=None) + timedelta(hours=1))]

    assert first_applied_at(events) == _T0 + timedelta(hours=1)
    assert first_applied_at([("create", _T0)]) is None
    assert aware(_T0) is _T0


def test_review_link_problem() -> None:
    applied = _T0
    after = _T0 + timedelta(minutes=5)

    assert review_link_problem("applied", applied, "succeeded", after, already_linked=False) is None
    assert review_link_problem("agreed", None, "succeeded", after, already_linked=False)
    assert review_link_problem("verified", applied, "succeeded", after, already_linked=False)
    assert review_link_problem("applied", applied, "succeeded", after, already_linked=True)
    assert review_link_problem("applied", applied, "failed", after, already_linked=False)
    before = _T0 - timedelta(minutes=5)
    assert review_link_problem("applied", applied, "succeeded", before, already_linked=False)


async def _run(
    session: AsyncSession, project: Project, *, status: str = "succeeded", shift: int = 0
) -> ReviewRun:
    """レビューの回(採用1件・未判断1件の指摘)。`shift`分だけ始めた時刻をずらす。"""
    run = await ReviewRunRepository(session).create_run(
        project_id=project.id, gate="documents", targets={}, trigger="manual", model="m"
    )
    run.status = status
    run.started_at = datetime.now(UTC) + timedelta(minutes=shift)
    for position, decision in enumerate(["adopted", "pending"]):
        run.findings.append(
            ReviewFinding(
                position=position,
                level="major",
                cause="spec",
                targets=[],
                problem="p",
                evidence="e",
                reason="r",
                suggestion="s",
                decision=decision,
            )
        )
    await session.commit()
    return run


async def _user(session: AsyncSession, project: Project) -> User:
    user = await session.get(User, project.user_id)
    assert user is not None
    return user


async def test_link_review_after_reflection(db_session: AsyncSession) -> None:
    project = await applied_change_request_project(db_session)
    user = await _user(db_session, project)
    await _run(db_session, project, shift=-60)  # 反映の前
    await _run(db_session, project, status="failed", shift=5)
    run = await _run(db_session, project, shift=10)

    candidates = await list_change_request_review_candidates(1, db_session, project)
    assert [c.review_run_id for c in candidates] == [run.id]
    assert isinstance(candidates[0], ReviewRunBriefRead)
    assert (candidates[0].findings, candidates[0].adopted) == (2, 1)

    read = await link_change_request_review(
        1, ChangeRequestReviewLink(review_run_id=run.id), db_session, project, user
    )

    assert read.status == "applied"
    assert isinstance(read.reviews[0], LinkedReviewRead)
    assert (read.reviews[0].review_run_id, read.reviews[0].linked_by) == (run.id, user.id)
    event = read.events[-1]
    assert (event.action, event.from_status, event.to_status) == (
        "link_review",
        "applied",
        "applied",
    )
    assert event.refs == {"review_run_id": str(run.id), "gate": "documents", "model": "m"}
    links = (await db_session.execute(select(ChangeRequestReview))).scalars().all()
    assert [link.review_run_id for link in links] == [run.id]
    # 結んだ回は候補から外れ、2回目は409
    assert await list_change_request_review_candidates(1, db_session, project) == []
    with pytest.raises(ChangeRequestReviewNotLinkableError):
        await link_change_request_review(
            1, ChangeRequestReviewLink(review_run_id=run.id), db_session, project, user
        )

    # 検証の時系列に、結んだレビューが残る
    verification = await get_change_request_verification(1, db_session, project)
    service = DesignStageService(db_session)
    for stage in {m.stage for m in verification.marks}:
        keys = [m.key for m in verification.marks if m.stage == stage]
        await service.review_marks(project, stage=stage, keys=keys, reviewed=True)
    await check_change_request_documents(
        1,
        DocumentChecksUpdate(keys=[d.key for d in verification.documents]),
        db_session,
        project,
    )
    verified = await verify_change_request(1, ChangeRequestVerify(), db_session, project, user)
    assert verified.events[-1].refs is not None
    assert verified.events[-1].refs["review_run_ids"] == [str(run.id)]


async def test_link_review_conflicts(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)
    user = await _user(db_session, project)
    run = await _run(db_session, project, shift=10)

    # 反映の前は候補が無く、結べない
    assert await list_change_request_review_candidates(1, db_session, project) == []
    with pytest.raises(ChangeRequestReviewNotLinkableError):
        await link_change_request_review(
            1, ChangeRequestReviewLink(review_run_id=run.id), db_session, project, user
        )
    with pytest.raises(ReviewRunNotFoundError):
        await link_change_request_review(
            1, ChangeRequestReviewLink(review_run_id=uuid.uuid4()), db_session, project, user
        )
