"""変更要求(CR)の検証の確かめ・文書の側の確認・検証済みへの遷移のテスト。

SUT: ChangeRequestService.verification・check_documents・verify と、今の状態を使う comparison
     (app/services/change_request_service.py)、スキーマ ChangeRequestVerificationRead・
     DocumentChecksUpdate・ChangeRequestVerify、ChangeRequestNotVerifiableError、
     ルート get_change_request_verification・check_change_request_documents・
     verify_change_request(app/api/routes/change_requests.py)、モデルの列 document_checks
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)を本物のリポジトリで使う。反映までは
tests/fixtures/change_requests.py(段階1〜8 承認済みの設計・合意済みの版・FakeLLM の再生成)
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_requests import (
    agreed_change_request_project,
    applied_change_request_project,
)

from app.api.routes.change_requests import (
    check_change_request_documents,
    get_change_request_comparison,
    get_change_request_verification,
    verify_change_request,
)
from app.models.change_request import ChangeRequest
from app.models.project import Project
from app.models.user import User
from app.schemas.change_request import (
    ChangeRequestVerificationRead,
    ChangeRequestVerify,
    DocumentChecksUpdate,
)
from app.services.change_request_service import ChangeRequestService
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    ChangeRequestInvalidTransitionError,
    ChangeRequestNotAppliedError,
    ChangeRequestNotVerifiableError,
)


async def _user(session: AsyncSession, project: Project) -> User:
    user = await session.get(User, project.user_id)
    assert user is not None
    return user


async def _review_all_marks(session: AsyncSession, project: Project) -> None:
    """反映で付いた印を、段階ごとにすべて確認済みにする。"""
    read = await get_change_request_verification(1, session, project)
    service = DesignStageService(session)
    for stage in {m.stage for m in read.marks}:
        keys = [m.key for m in read.marks if m.stage == stage]
        await service.review_marks(project, stage=stage, keys=keys, reviewed=True)


async def test_verification_lists_open_marks_and_documents(db_session: AsyncSession) -> None:
    project = await applied_change_request_project(db_session)

    read = await get_change_request_verification(1, db_session, project)

    assert isinstance(read, ChangeRequestVerificationRead)
    assert (read.label, read.status, read.can_verify) == ("CR-1", "applied", False)
    assert read.marks and all(m.confirmation == "open" for m in read.marks)
    assert read.marks[0].confirmation_label == "未確認"
    assert read.open_marks == len(read.marks)
    # 文書の側(画面・API)は、保存した予測から確認の一覧になる
    assert read.documents and not any(d.reviewed for d in read.documents)
    assert read.open_documents == len(read.documents)


async def test_verify_after_every_mark_and_document_is_checked(
    db_session: AsyncSession,
) -> None:
    project = await applied_change_request_project(db_session)
    user = await _user(db_session, project)
    with pytest.raises(ChangeRequestNotVerifiableError):  # 未確認が残っている
        await verify_change_request(1, ChangeRequestVerify(), db_session, project, user)

    await _review_all_marks(db_session, project)
    pending = await get_change_request_verification(1, db_session, project)
    assert pending.open_marks == 0 and pending.open_documents > 0
    keys = [d.key for d in pending.documents]
    checked = await check_change_request_documents(
        1, DocumentChecksUpdate(keys=[*keys, "scr:無い"]), db_session, project
    )
    assert checked.can_verify and checked.open_documents == 0
    stored = await db_session.get(ChangeRequest, (await _cr(db_session, project)).id)
    assert stored is not None and stored.document_checks == keys  # 知らないキーは残らない

    # 比較も今の確認の状態を出す
    comparison = await get_change_request_comparison(1, db_session, project)
    assert all(i.reviewed for i in comparison.items if i.reviewed is not None)
    assert any(i.stage == 0 and i.reviewed for i in comparison.items)

    read = await verify_change_request(
        1, ChangeRequestVerify(note=" 確かめた "), db_session, project, user
    )

    assert (read.status, read.allowed_actions) == ("verified", [])
    event = read.events[-1]
    assert (event.action, event.from_status, event.to_status, event.note) == (
        "verify",
        "applied",
        "verified",
        "確かめた",
    )
    assert event.refs == {
        "mark_count": len(pending.marks),
        "document_count": len(keys),
        "review_run_ids": [],
    }
    with pytest.raises(ChangeRequestInvalidTransitionError):  # 検証済みからは確認を変えない
        await check_change_request_documents(
            1, DocumentChecksUpdate(keys=keys, reviewed=False), db_session, project
        )


async def test_marks_cleared_by_approval_count_as_confirmed(db_session: AsyncSession) -> None:
    project = await applied_change_request_project(db_session)
    await _review_all_marks(db_session, project)
    read = await get_change_request_verification(1, db_session, project)
    stage = read.marks[0].stage
    service = DesignStageService(db_session)
    # 段階を開き直して(下書き)、承認し直す。承認で段階の印は消える
    assert await service.mark_edited(project.id, stage)
    await db_session.commit()
    row, _, _ = await service.stage_view(project, stage)
    assert row is not None

    await service.approve(project, stage=stage, expected_version=row.version)

    after = await get_change_request_verification(1, db_session, project)
    by_key = {m.key: m.confirmation for m in after.marks}
    assert {by_key[m.key] for m in read.marks if m.stage == stage} == {"approved"}


async def test_unchecking_document_and_new_mark_reopen(db_session: AsyncSession) -> None:
    project = await applied_change_request_project(db_session)
    await _review_all_marks(db_session, project)
    read = await get_change_request_verification(1, db_session, project)
    first = read.marks[0]
    keys = [d.key for d in read.documents]
    await check_change_request_documents(1, DocumentChecksUpdate(keys=keys), db_session, project)

    unchecked = await check_change_request_documents(
        1, DocumentChecksUpdate(keys=keys[:1], reviewed=False), db_session, project
    )
    assert unchecked.open_documents == 1
    # 印を未確認に戻すと、もう一度確かめるまで検証できない
    await DesignStageService(db_session).review_marks(
        project, stage=first.stage, keys=[first.key], reviewed=False
    )
    reopened = await get_change_request_verification(1, db_session, project)
    assert (reopened.open_marks, reopened.can_verify) == (1, False)


async def test_verification_before_reflection_is_conflict(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)
    with pytest.raises(ChangeRequestNotAppliedError):
        await get_change_request_verification(1, db_session, project)
    with pytest.raises(ChangeRequestInvalidTransitionError):
        await check_change_request_documents(
            1, DocumentChecksUpdate(keys=["scr:SCR-001"]), db_session, project
        )
    user = await _user(db_session, project)
    with pytest.raises(ChangeRequestInvalidTransitionError):  # 合意からは検証できない
        await verify_change_request(1, ChangeRequestVerify(), db_session, project, user)


async def _cr(session: AsyncSession, project: Project) -> ChangeRequest:
    return (await ChangeRequestService(session).get(project, 1)).change_request
