"""資料の違いから CR の下書きを作るテスト。

SUT: MaterialDifferenceService.create_change_request・一覧の候補と CR の番号、
     ChangeRequestService.add_draft、MaterialDifferenceRepository(get_many_in_material・
     change_request_numbers)、ルート create_change_request_from_differences と
     ChangeRequestRead.from_view、MaterialChangeRequestCreate、モデル MaterialDifference の
     change_request_id
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ: 取り出しの LLM を、決まった構造化出力を返す FakeLLM に差し替えてサービスに注入する。束・CR は
     偽の LLM で反映する fixture(tests/fixtures/change_requests.py)で作り、DB はテスト用の SQLite
"""

import uuid
from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_requests import agreed_change_request_project
from tests.fixtures.fake_llm import FakeLLM

from app.api.routes.materials import create_change_request_from_differences
from app.materials import DifferenceItemOut, DifferenceListOut
from app.models.change_request import ChangeRequest
from app.models.material_difference import MaterialDifference
from app.models.project import Project
from app.models.user import User
from app.repositories.material_difference import MaterialDifferenceRepository
from app.schemas.material import MaterialCreate
from app.schemas.material_difference import MaterialChangeRequestCreate
from app.services.errors import (
    MaterialDifferenceNotFoundError,
    MaterialDifferenceOutdatedError,
    MaterialDifferenceStateError,
    MaterialDraftInvalidError,
)
from app.services.material_difference_service import MaterialDifferenceService
from app.services.material_service import MaterialService

_BODY = """# 第5回 定例
- 予約の登録の後に、確認メールを送ってほしい。
- 予約の画面では、日付の新しい順に並べてほしい。
- 予約の画面に、空き状況の表を足してほしい。
- 取消の期限は次回までに決める。
"""
_CHANGE = {
    "kind": "change",
    "target_key": "req:FR-001",
    "content": "登録の後に確認メールを送る",
    "quote": "予約の登録の後に、確認メールを送ってほしい。",
}
_SCREEN = {
    "kind": "change",
    "target_key": "screen:SCR-001",
    "content": "一覧を日付の新しい順にする",
    "quote": "予約の画面では、日付の新しい順に並べてほしい。",
}
_ADD = {
    "kind": "add",
    "origin_key": "screen:SCR-001",
    "content": "空き状況の表を足す",
    "quote": "予約の画面に、空き状況の表を足してほしい。",
}
_QUESTION = {
    "kind": "question",
    "content": "取消の期限が未定",
    "quote": "取消の期限は次回までに決める。",
}


async def _extracted(
    session: AsyncSession, project: Project, *items: dict[str, str]
) -> tuple[uuid.UUID, list[MaterialDifference], MaterialDifferenceService]:
    payload = MaterialCreate(
        kind="minutes", title="第5回 定例", material_date=date(2026, 10, 28), body=_BODY
    )
    material = (
        await MaterialService(session).create_from_text(project, payload, created_by=None)
    ).material
    out = DifferenceListOut(differences=[DifferenceItemOut.model_validate(i) for i in items])
    service = MaterialDifferenceService(session, llm=FakeLLM(structured_sequence=[out, out]))
    view = await service.extract(project, material.id, user_id=project.user_id)
    return material.id, [d.difference for d in view.differences], service


async def test_differences_become_one_draft_and_are_converted(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)
    material_id, (change, add, _), service = await _extracted(
        db_session, project, _CHANGE, _ADD, _QUESTION
    )

    view = await service.create_change_request(
        project, material_id, [change.id, add.id], requirement_ids=None, created_by=project.user_id
    )

    cr = view.change_request
    assert (cr.number, cr.status, cr.baseline.number) == (2, "draft", 1)
    assert cr.title == "「第5回 定例」の変更(2件)"
    assert "- 変更 req:FR-001: 登録の後に確認メールを送る" in cr.content
    assert "- 追加 新規(起点 screen:SCR-001): 空き状況の表を足す" in cr.content
    assert (cr.background_source, cr.background_date) == ("meeting", date(2026, 10, 28))
    assert cr.background_memo == "資料「第5回 定例」(2026-10-28)の違い2件から作った下書き"
    assert cr.targets == [{"requirement_id": "FR-001", "kind": "content", "proposed_text": ""}]
    assert cr.adds_requirement
    assert cr.origins == [{"key": "screen:SCR-001", "label": "SCR-001 予約画面"}]
    # 同じ要件を対象にする CR-1 が進行中なので、重なりとして示す(作成は止めない)
    assert [o.number for o in view.overlapping] == [1]

    listed = await service.list_for(project, material_id)
    assert [
        (d.difference.status, d.change_request_number, d.difference.decided_by)
        for d in listed.differences
    ] == [
        ("converted", 2, project.user_id),
        ("converted", 2, project.user_id),
        ("unconfirmed", None, None),
    ]
    # やり直しても、CR にした違いは残り、同じ違いは足し直さない
    rerun = await service.extract(project, material_id, user_id=project.user_id)
    assert [(d.difference.kind, d.difference.status) for d in rerun.differences] == [
        ("change", "converted"),
        ("add", "converted"),
        ("question", "unconfirmed"),
    ]


async def test_screen_difference_targets_chosen_requirements(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)
    material_id, (screen,), service = await _extracted(db_session, project, _SCREEN)
    listed = await service.list_for(project, material_id)
    assert listed.differences[0].requirement_candidates == ("FR-001", "FR-002")

    with pytest.raises(MaterialDraftInvalidError):
        await service.create_change_request(
            project, material_id, [screen.id], requirement_ids=["NFR-001"], created_by=None
        )
    view = await service.create_change_request(
        project, material_id, [screen.id], requirement_ids=["FR-002"], created_by=None
    )

    assert view.change_request.title == "一覧を日付の新しい順にする"
    assert view.change_request.targets == [
        {"requirement_id": "FR-002", "kind": "content", "proposed_text": ""}
    ]
    assert not view.change_request.adds_requirement


async def test_only_unconfirmed_changes_of_current_bundle_are_accepted(
    db_session: AsyncSession,
) -> None:
    project = await agreed_change_request_project(db_session)
    material_id, (change, question), service = await _extracted(
        db_session, project, _CHANGE, _QUESTION
    )
    other_id, (other,), _ = await _extracted(db_session, project, _SCREEN)

    with pytest.raises(MaterialDifferenceStateError):
        await service.create_change_request(
            project, material_id, [question.id], requirement_ids=None, created_by=None
        )
    with pytest.raises(MaterialDifferenceNotFoundError):
        await service.create_change_request(
            project, material_id, [change.id, other.id], requirement_ids=None, created_by=None
        )
    # 違いを取り出した後に束を作り直した(違いの束が最新の束でない)
    change.baseline_number = 0
    await db_session.flush()
    with pytest.raises(MaterialDifferenceOutdatedError):
        await service.create_change_request(
            project, material_id, [change.id], requirement_ids=None, created_by=None
        )
    change.baseline_number = 1
    await service.create_change_request(
        project, material_id, [change.id], requirement_ids=None, created_by=None
    )
    with pytest.raises(MaterialDifferenceStateError):
        await service.create_change_request(
            project, material_id, [change.id], requirement_ids=None, created_by=None
        )
    assert other_id != material_id


async def test_route_returns_change_request_and_numbers(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)
    material_id, (change,), _ = await _extracted(db_session, project, _CHANGE)
    user = await db_session.get(User, project.user_id)
    assert user is not None

    read = await create_change_request_from_differences(
        material_id,
        MaterialChangeRequestCreate(difference_ids=[change.id, change.id]),
        db_session,
        project,
        user,
    )

    assert (read.number, read.label, read.status) == (2, "CR-2", "draft")
    assert read.background_source == "meeting"
    numbers = await MaterialDifferenceRepository(db_session).change_request_numbers([change])
    assert numbers == {change.id: 2}
    created = await db_session.get(ChangeRequest, change.change_request_id)
    assert created is not None and created.number == 2


def test_create_payload_needs_differences() -> None:
    with pytest.raises(ValidationError):
        MaterialChangeRequestCreate(difference_ids=[])
    assert MaterialChangeRequestCreate(difference_ids=[uuid.uuid4()]).requirement_ids is None
