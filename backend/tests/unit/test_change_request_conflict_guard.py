"""重なる CR の競合のガード(反映の受け付け・確かめの印・指示)のテスト。

SUT: ChangeRequestService.check_conflicts・require_conflicts_checked・_view の`conflicts`・
     reflection の指示(app/services/change_request_service.py)、反映の受け付け
     DocGeneratorService.request_generation(app/services/doc_generator_service.py)、
     ChangeRequestConflictUncheckedError、応答 ChangeRequestRead.conflicts、モデルの列
     `conflict_checks`
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)を本物のリポジトリで使う。設計は段階1〜8 を承認済みに
した fixture(`create_impact_project`)から合意済みの版を作る。文書の生成の LLM は FakeLLM
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.requirements import requirements_output

from app.models.project import Project
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestRead,
)
from app.services.baseline_service import BaselineService
from app.services.change_request_service import ChangeRequestService, ChangeRequestView
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import (
    ChangeRequestConflictUncheckedError,
    ChangeRequestInvalidTransitionError,
)

_AT = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
_CR1_TEXT = "予約を登録し、確認メールを送る"


async def _agreed_cr(session: AsyncSession, project: Project, title: str, text: str) -> int:
    service = ChangeRequestService(session)
    view = await service.create(
        project,
        ChangeRequestCreate.model_validate(
            {
                "title": title,
                "content": title,
                "background_source": "meeting",
                "targets": [{"requirement_id": "FR-001", "kind": "content", "proposed_text": text}],
            }
        ),
        created_by=None,
    )
    number = view.change_request.number
    await service.explain(project, number, actor_id=None)
    await service.decide(
        project,
        number,
        ChangeRequestDecision.model_validate(
            {"decision": "agreed", "decided_at": _AT, "reason": "合意"}
        ),
        actor_id=None,
    )
    return number


async def _apply(session: AsyncSession, project: Project, number: int, text: str) -> FakeLLM:
    llm = FakeLLM(
        content_sequence=["## 自己診断\n最重要: 特になし"],
        structured_by_schema=upstream_outputs(requirements_output(prev_id="FR-001", text=text)),
    )
    service = DocGeneratorService(session)
    status = await service.request_generation(project, start_from="requirements", cr_number=number)
    await service.generate(
        project.id,
        project.user_id,
        status_before_generation=status,
        start_from="requirements",
        cr_number=number,
        llm=llm,
    )
    return llm


async def _two_crs_with_cr1_applied(session: AsyncSession) -> Project:
    """B-1 を基準に、FR-001 を対象にする CR-1・CR-2(どちらも合意)。CR-1 だけ反映した。"""
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    await _agreed_cr(session, project, "確認メールを送る", _CR1_TEXT)
    await _agreed_cr(session, project, "登録を2段階にする", "予約を仮登録し、確定する")
    await _apply(session, project, 1, _CR1_TEXT)
    return project


def _read(view: ChangeRequestView) -> ChangeRequestRead:
    return ChangeRequestRead.build(
        view.change_request,
        overlapping=view.overlapping,
        derived_from_number=view.derived_from_number,
        derived_numbers=view.derived_numbers,
        allowed_actions=view.allowed_actions,
        conflicts=view.conflicts,
    )


async def test_detail_shows_conflict_with_base_and_current_text(db_session: AsyncSession) -> None:
    project = await _two_crs_with_cr1_applied(db_session)
    service = ChangeRequestService(db_session)

    read = _read(await service.get(project, 2))

    [conflict] = read.conflicts
    assert (conflict.requirement_id, conflict.by, conflict.checked) == ("FR-001", ["CR-1"], False)
    assert conflict.current_text == _CR1_TEXT
    assert conflict.base_text and conflict.base_text != _CR1_TEXT
    # 反映した CR-1 自身には競合が無い
    assert _read(await service.get(project, 1)).conflicts == []


async def test_apply_needs_check_and_check_expires_on_later_change(
    db_session: AsyncSession,
) -> None:
    project = await _two_crs_with_cr1_applied(db_session)
    service = ChangeRequestService(db_session)

    with pytest.raises(ChangeRequestConflictUncheckedError) as raised:
        await DocGeneratorService(db_session).request_generation(
            project, start_from="requirements", cr_number=2
        )
    assert "CR-1 が先に変えた" in str(raised.value)

    view = await service.check_conflicts(project, 2, actor_id=None)
    assert [checked for _, checked in view.conflicts] == [True]
    assert view.change_request.conflict_checks
    assert view.change_request.events[-1].action == "check_conflict"
    await service.require_conflicts_checked(project, 2)

    # CR-1 をやり直すと、FR-001 の合意の記録が増え、印は効かなくなる
    await _apply(db_session, project, 1, "予約を登録し、確認メールとSMSを送る")
    with pytest.raises(ChangeRequestConflictUncheckedError):
        await service.require_conflicts_checked(project, 2)


async def test_reflection_instruction_carries_current_text(db_session: AsyncSession) -> None:
    project = await _two_crs_with_cr1_applied(db_session)
    await ChangeRequestService(db_session).check_conflicts(project, 2, actor_id=None)

    llm = await _apply(db_session, project, 2, "予約を仮登録し、確定する")

    human = str(llm.structured_messages[0][-1].content)
    assert f"今の文は CR-1 の反映の後のもの「{_CR1_TEXT}」" in human


async def test_check_without_conflicts_is_rejected(db_session: AsyncSession) -> None:
    project = await _two_crs_with_cr1_applied(db_session)

    with pytest.raises(ChangeRequestInvalidTransitionError):
        await ChangeRequestService(db_session).check_conflicts(project, 1, actor_id=None)
