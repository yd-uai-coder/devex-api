"""検証済みの変更要求(CR)を合意済みの版(束)に入れることと、受付から検証までをたどる記録のテスト。

SUT: 純粋関数 trace_entries と ACTION_LABEL(app/change_requests/trace.py)、
     ChangeRequestService.trace と review_facts(app/services/change_request_service.py)、
     BaselineService.create の`change_request_numbers`(app/services/baseline_service.py)、
     スキーマ BaselineCreate・ChangeRequestTraceRead・TraceEntryRead、
     ChangeRequestNotIncludableError、
     ルート get_change_request_trace・list_change_requests(一覧の入れた束。
     app/api/routes/change_requests.py)・
     create_baseline(app/api/routes/baselines.py)、モデルの列 included_baseline_id
ドライバ: 各テスト関数
スタブ: 純粋関数のテストはスタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため。
サービスとルートは、インメモリ SQLite(`db_session`)と tests/fixtures/change_requests.py
(反映まで・確認をすべて済ませる)。レビューの回は LLM を呼ばず、リポジトリで直接作る
"""

from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_requests import (
    AGREED_AT,
    applied_change_request_project,
    confirm_everything,
)

from app.api.routes.baselines import create_baseline
from app.api.routes.change_requests import get_change_request_trace, list_change_requests
from app.change_requests import (
    ACTION_LABEL,
    ChangeRequestAction,
    EventFacts,
    ReviewFacts,
    TraceDocument,
    TraceStage,
    trace_entries,
)
from app.models.project import Project
from app.models.user import User
from app.repositories.review import ReviewRunRepository
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import ChangeRequestTraceRead, ChangeRequestVerify
from app.services.change_request_service import ChangeRequestService
from app.services.errors import ChangeRequestNotFoundError, ChangeRequestNotIncludableError

_T = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _event(action: str, refs: dict | None = None) -> EventFacts:
    return EventFacts(action, "agreed", "applied", _T, None, "", refs)


def test_trace_entries_reads_refs() -> None:
    review = ReviewFacts("run-1", "documents", "m", "succeeded", 3, 1)
    events = [
        _event("create"),
        _event(
            "apply",
            {
                "documents": [
                    {"doc_type": "requirements", "version": 3},
                    {"doc_type": "external_design", "version": 1},
                    {"version": "壊れた行"},
                ],
                "model": "gemini",
            },
        ),
        _event("apply", {"stage": 2, "version": 4, "model": "claude"}),
        _event("link_review", {"review_run_id": "run-1", "gate": "documents", "model": "m"}),
        _event(
            "verify",
            {"mark_count": 3, "document_count": 1, "review_run_ids": ["run-1", "消えた回"]},
        ),
        _event("baseline", {"baseline_number": 2}),
    ]

    entries = trace_entries(events, {"requirements": {2, 3}}, {"run-1": review})

    assert [e.position for e in entries] == list(range(6))
    assert entries[0].action_label == "作成"
    assert entries[1].documents == (
        TraceDocument("requirements", "要件定義書", 3, True),
        TraceDocument("external_design", "外部設計書", 1, False),  # もう残っていない版
    )
    assert entries[1].model == "gemini"
    assert (entries[2].stage, entries[2].model) == (TraceStage(2, 4), "claude")
    assert entries[3].reviews == (review,)
    assert entries[4].reviews == (review,)  # 結んでいない(消えた)回は読まない
    assert entries[5].baseline_number == 2
    assert entries[5].action_label == "合意済みの版に入れた"


def test_action_labels_cover_every_action() -> None:
    assert set(ACTION_LABEL) == set(get_args(ChangeRequestAction))


def test_baseline_create_dedupes_numbers() -> None:
    payload = BaselineCreate(agreed_at=AGREED_AT, reason="合意", change_request_numbers=[2, 1, 2])
    assert payload.change_request_numbers == [2, 1]
    with pytest.raises(ValidationError):
        BaselineCreate(agreed_at=AGREED_AT, reason="合意", change_request_numbers=[0])


async def _user(session: AsyncSession, project: Project) -> User:
    user = await session.get(User, project.user_id)
    assert user is not None
    return user


async def test_verified_change_request_goes_into_new_baseline_and_trace(
    db_session: AsyncSession,
) -> None:
    project = await applied_change_request_project(db_session)
    user = await _user(db_session, project)
    service = ChangeRequestService(db_session)
    run = await ReviewRunRepository(db_session).create_run(
        project_id=project.id, gate="documents", targets={}, trigger="manual", model="m"
    )
    run.status = "succeeded"
    await db_session.commit()
    await service.link_review(project, 1, run.id, actor_id=user.id)
    with pytest.raises(ChangeRequestNotIncludableError):  # 検証の前は束に入れない
        await create_baseline(
            BaselineCreate(agreed_at=AGREED_AT, reason="合意", change_request_numbers=[1]),
            db_session,
            project,
            user,
        )
    await confirm_everything(db_session, project)
    await service.verify(project, 1, ChangeRequestVerify(), actor_id=user.id)

    baseline = await create_baseline(
        BaselineCreate(agreed_at=AGREED_AT, reason="CR-1 を反映した版", change_request_numbers=[1]),
        db_session,
        project,
        user,
    )

    assert baseline.number == 2
    detail = await service.get(project, 1)
    assert detail.change_request.status == "verified"
    trace = await get_change_request_trace(1, db_session, project)
    assert isinstance(trace, ChangeRequestTraceRead)
    assert (trace.label, trace.baseline_number, trace.included_baseline_number) == ("CR-1", 1, 2)
    # 一覧も入れた束を持つ(束の作成で、まだ入れていない検証済みの CR を選ぶ)
    listed = await list_change_requests(db_session, project)
    assert [(c.label, c.included_baseline_number) for c in listed] == [("CR-1", 2)]
    actions = [e.action for e in trace.entries]
    assert actions == ["create", "explain", "agree", "apply", "link_review", "verify", "baseline"]
    apply = trace.entries[3]
    assert [(d.doc_type, d.available) for d in apply.documents] == [
        ("requirements", True),
        ("external_design", True),
    ]
    assert trace.entries[4].reviews[0].review_run_id == str(run.id)
    assert trace.entries[5].reviews[0].gate == "documents"
    last = trace.entries[-1]
    assert (last.baseline_number, last.to_status, last.actor_id) == (2, "verified", user.id)
    # 2回目は、もう束に入っているので409
    with pytest.raises(ChangeRequestNotIncludableError):
        await create_baseline(
            BaselineCreate(agreed_at=AGREED_AT, reason="合意", change_request_numbers=[1]),
            db_session,
            project,
            user,
        )
    with pytest.raises(ChangeRequestNotFoundError):
        await create_baseline(
            BaselineCreate(agreed_at=AGREED_AT, reason="合意", change_request_numbers=[9]),
            db_session,
            project,
            user,
        )
