"""変更要求(CR)を渡して作り直した段階の承認で、付く印を CR に結ぶテスト。

SUT: 純粋関数 stage_apply_versions・pending_stages(app/change_requests/stage_approvals.py)、
     merge_applied_marks(app/change_requests/comparison.py)、verification_of の`pending`と
     Verification.complete(app/change_requests/verification.py)、たどる記録の approve_stage の行
     (app/change_requests/trace.py)、DesignStageService.approve の`actor_id`と CR に結ぶ処理
     (app/services/design_stage_service.py)、ChangeRequestService.verification・verify の
     作り直した段階の確かめ(app/services/change_request_service.py)、スキーマ
     ChangeRequestVerificationRead.pending_stages、ルート approve_design_stage
     (app/api/routes/design_stages.py)
ドライバ: 各テスト関数
スタブ: 純粋関数はスタブ不要 ── 渡した値だけから決まり、DB にも LLM にも触れないため。サービスは
DB をインメモリ SQLite(`db_session`)で本物のリポジトリを使い、反映までは
tests/fixtures/change_requests.py(段階1〜8 承認済みの設計・合意済みの版・FakeLLM の再生成)、
段階の作り直しの LLM は FakeLLM(段階1の構造化出力)
"""

from datetime import UTC, datetime

import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_requests import applied_change_request_project, confirm_everything
from tests.fixtures.fake_llm import FakeLLM

from app.api.routes.design_stages import approve_design_stage
from app.change_requests import (
    APPROVE_STAGE_ACTION,
    AppliedMark,
    EventFacts,
    Prediction,
    StageFacts,
    pending_stages,
    stage_apply_versions,
    trace_entries,
    verification_of,
)
from app.detailed_design.drafting import FunctionListGenerationOutput, GeneratedFunction
from app.detailed_design.impact import StageImpact, cr_source
from app.models.project import Project
from app.models.user import User
from app.repositories.design_stage import DesignStageRepository
from app.schemas.change_request import ChangeRequestVerificationRead, ChangeRequestVerify
from app.schemas.design_stage import DesignStageApprove
from app.services.change_request_service import ChangeRequestService
from app.services.design_stage_generation_service import DesignStageGenerationService
from app.services.design_stage_service import DesignStageService
from app.services.errors import ChangeRequestNotVerifiableError

_AT = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


# ---- 純粋関数 ----


def test_stage_apply_versions_reads_the_latest_stage_apply() -> None:
    events = [
        ("apply", {"documents": [{"doc_type": "requirements", "version": 2}], "model": "m"}),
        ("apply", {"stage": 1, "version": 3, "model": "m"}),
        ("edit", {"stage": 4, "version": 9}),  # 段階の反映の行でない
        ("apply", {"stage": 1, "version": 5, "model": "m"}),
        ("apply", {"stage": 2, "version": "x"}),  # 形の正しくない行は読まない
        ("apply", None),
    ]

    assert stage_apply_versions(events) == {1: 5}


def test_pending_stages_until_the_regenerated_version_is_approved() -> None:
    cr, other = "cr-1", "cr-2"
    applied = {1: 5, 2: 4, 3: 2, 4: 7}
    rows = [
        StageFacts(1, cr, 3),  # 作り直した版(5)より前の承認 → 未承認
        StageFacts(2, cr, 4),  # 作り直した版を承認した
        StageFacts(3, other, None),  # 後で別の CR で作り直した
        StageFacts(4, None, 1),  # 後で CR なしで作り直した
        StageFacts(5, cr, None),  # この CR の反映の行が無い
        StageFacts(6, cr, None),
    ]

    assert pending_stages(cr, applied | {6: 1}, rows) == [1, 6]
    assert pending_stages(cr, {}, rows) == []


def test_verification_is_incomplete_while_a_stage_is_pending() -> None:
    applied = [AppliedMark("function:F-01", "F-01", 1, "理由", reviewed=False)]
    current = {1: StageImpact()}  # 印は承認で済んだ
    prediction = Prediction(targets=(), items=(), unpredicted_new_requirement=False)

    done = verification_of(applied, current, 1, prediction, [])
    pending = verification_of(applied, current, 1, prediction, [], [3, 2])

    assert done.complete
    assert pending.pending_stages == (2, 3)
    assert not pending.complete


def test_trace_reads_the_stage_of_an_approval_row() -> None:
    event = EventFacts(
        action=APPROVE_STAGE_ACTION,
        from_status="applied",
        to_status="applied",
        occurred_at=_AT,
        actor_id=None,
        note="段階1の承認で印を2件結んだ",
        refs={"stage": 1, "version": 4, "mark_count": 2},
    )

    [entry] = trace_entries([event], {}, {})

    assert entry.action_label == "段階の承認で印を結んだ"
    assert entry.stage is not None
    assert (entry.stage.stage, entry.stage.version) == (1, 4)


# ---- サービス ----


def _llm() -> FakeLLM:
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
    return FakeLLM(structured_sequence=[output])


async def _regenerate_stage1(
    session: AsyncSession, project: Project, *, cr_number: int | None = 1
) -> None:
    service = DesignStageGenerationService(session)
    await service.request_generation(project, stage=1, cr_number=cr_number)
    await service.execute(
        project_id=project.id,
        user_id=project.user_id,
        stage=1,
        cr_number=cr_number,
        llm=_llm(),
    )


async def _checked_project(session: AsyncSession) -> Project:
    """反映済みの CR-1 で、反映で付いた印と文書の側の確認をすべて済ませたプロジェクト。"""
    project = await applied_change_request_project(session)
    await confirm_everything(session, project)
    return project


async def test_stage_regenerated_with_cr_blocks_verification_until_approved(
    db_session: AsyncSession,
) -> None:
    project = await _checked_project(db_session)
    service = ChangeRequestService(db_session)
    assert (await service.verification(project, 1)).verification.complete

    await _regenerate_stage1(db_session, project)

    view = await service.verification(project, 1)
    assert view.verification.pending_stages == (1,)
    read = ChangeRequestVerificationRead.build(
        view.change_request, view.verification, can_verify=False
    )
    assert read.pending_stages == [1]
    with pytest.raises(ChangeRequestNotVerifiableError, match="段階1の承認"):
        await service.verify(project, 1, ChangeRequestVerify(), actor_id=None)


async def test_approval_links_its_marks_to_the_change_request(db_session: AsyncSession) -> None:
    project = await _checked_project(db_session)
    await _regenerate_stage1(db_session, project)
    stage = await DesignStageService(db_session).read(project.id, 1)
    user = await db_session.get(User, project.user_id)
    assert user is not None
    payload = DesignStageApprove(version=stage.version or 0)

    await approve_design_stage(1, payload, db_session, project, user, BackgroundTasks())

    cr = (await ChangeRequestService(db_session).get(project, 1)).change_request
    last = cr.events[-1]
    assert (last.action, last.from_status, last.to_status) == (
        APPROVE_STAGE_ACTION,
        "applied",
        "applied",
    )
    assert last.actor_id == user.id
    assert last.refs is not None
    assert last.refs["stage"] == 1 and last.refs["version"] == stage.version
    count = last.refs["mark_count"]
    assert count > 0
    assert last.note == f"段階1の承認で印を{count}件結んだ"
    # 付いた印は出どころに cr:1 を持ち、CR の印の写しに重なる
    stages = await DesignStageRepository(db_session).list_for_project(project.id)
    linked = {
        m.key
        for s in stages
        for m in StageImpact.from_dict(s.impact).marks
        if cr_source(1) in m.sources and "stage:1" in m.sources
    }
    assert linked and linked <= {m["key"] for m in cr.applied_impact or []}
    view = await ChangeRequestService(db_session).verification(project, 1)
    assert view.verification.pending_stages == ()
    assert view.verification.open_marks > 0  # 結んだ印を確かめるまで検証できない


async def test_approval_without_cr_regeneration_links_nothing(db_session: AsyncSession) -> None:
    project = await _checked_project(db_session)
    await _regenerate_stage1(db_session, project, cr_number=None)
    stage = await DesignStageService(db_session).read(project.id, 1)

    await DesignStageService(db_session).approve(
        project, stage=1, expected_version=stage.version or 0
    )

    cr = (await ChangeRequestService(db_session).get(project, 1)).change_request
    assert APPROVE_STAGE_ACTION not in [e.action for e in cr.events]
    view = await ChangeRequestService(db_session).verification(project, 1)
    assert view.verification.pending_stages == ()

