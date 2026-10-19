"""変更要求(CR)と時系列の表とリポジトリのテスト。

SUT: ChangeRequestRepository(next_number / create / add_event / list_for_project /
     get_by_number / list_by_statuses / find_derived)と、ChangeRequest・ChangeRequestEvent の
     ORM 定義
ドライバ: pytest のテスト関数
スタブ不要 ── 永続化そのものが検証対象のため、テスト用の SQLite のセッションを使う(外部キーの
CASCADE は SQLite では効かないので、プロジェクトの削除で消えることは統合テストで確かめる)
"""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.baseline import Baseline
from app.models.change_request import ChangeRequest, ChangeRequestEvent
from app.models.project import Project
from app.models.user import User
from app.repositories.baseline import BaselineRepository
from app.repositories.change_request import ChangeRequestRepository


async def _project_and_baseline(session: AsyncSession) -> tuple[Project, Baseline]:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="detailed")
    session.add(project)
    await session.flush()
    baseline = await BaselineRepository(session).create(
        project_id=project.id,
        number=1,
        agreed_at=datetime(2026, 10, 19, tzinfo=UTC),
        reason="r",
        items=[],
    )
    return project, baseline


async def _create(
    repository: ChangeRequestRepository,
    project: Project,
    baseline: Baseline,
    number: int,
    **overrides: object,
) -> ChangeRequest:
    values: dict[str, object] = {
        "project_id": project.id,
        "number": number,
        "baseline_id": baseline.id,
        "title": "参考のファイルを5つまで",
        "content": "3つから5つへ",
        "background_source": "meeting",
        "status": "draft",
        "action": "create",
    }
    values.update(overrides)
    return await repository.create(**values)  # type: ignore[arg-type]


async def test_numbers_are_sequential_per_project(db_session: AsyncSession) -> None:
    repository = ChangeRequestRepository(db_session)
    project, baseline = await _project_and_baseline(db_session)

    assert await repository.next_number(project.id) == 1
    await _create(repository, project, baseline, 1)

    assert await repository.next_number(project.id) == 2


async def test_create_keeps_fields_and_first_event(db_session: AsyncSession) -> None:
    project, baseline = await _project_and_baseline(db_session)
    repository = ChangeRequestRepository(db_session)

    created = await _create(
        repository,
        project,
        baseline,
        1,
        background_date=date(2026, 10, 18),
        background_memo="定例",
        target_requirement_ids=["FR-005"],
        adds_requirement=True,
        created_by=project.user_id,
    )
    await db_session.commit()
    db_session.expunge_all()

    loaded = await repository.get_by_number(project_id=project.id, number=1)
    assert loaded is not None and loaded.id == created.id
    assert (loaded.background_date, loaded.background_memo) == (date(2026, 10, 18), "定例")
    assert (loaded.target_requirement_ids, loaded.adds_requirement) == (["FR-005"], True)
    assert loaded.baseline.number == 1
    assert loaded.decided_at is None
    event: ChangeRequestEvent = loaded.events[0]
    assert (event.action, event.from_status, event.to_status, event.actor_id) == (
        "create",
        None,
        "draft",
        project.user_id,
    )


async def test_add_event_moves_status_and_keeps_order(db_session: AsyncSession) -> None:
    project, baseline = await _project_and_baseline(db_session)
    repository = ChangeRequestRepository(db_session)
    change_request = await _create(repository, project, baseline, 1)

    await repository.add_event(change_request, action="explain", to_status="explained")
    await repository.add_event(
        change_request, action="withdraw", to_status="withdrawn", note="不要になった"
    )
    await db_session.commit()
    db_session.expunge_all()

    loaded = await repository.get_by_number(project_id=project.id, number=1)
    assert loaded is not None and loaded.status == "withdrawn"
    assert [(e.position, e.from_status, e.to_status) for e in loaded.events] == [
        (0, None, "draft"),
        (1, "draft", "explained"),
        (2, "explained", "withdrawn"),
    ]
    assert loaded.events[2].note == "不要になった"


async def test_list_filters_and_derived(db_session: AsyncSession) -> None:
    project, baseline = await _project_and_baseline(db_session)
    repository = ChangeRequestRepository(db_session)
    first = await _create(repository, project, baseline, 1, status="rejected")
    await _create(repository, project, baseline, 2)
    await _create(repository, project, baseline, 3, derived_from_id=first.id, action="copy")

    listed = await repository.list_for_project(project.id)
    active = await repository.list_by_statuses(project.id, ["draft"])
    derived = await repository.find_derived(first.id)

    assert [c.number for c in listed] == [3, 2, 1]
    assert [c.number for c in active] == [2, 3]
    assert [c.number for c in derived] == [3]
    assert await repository.get_by_number(project_id=project.id, number=9) is None


async def test_duplicate_number_is_rejected_by_db(db_session: AsyncSession) -> None:
    project, baseline = await _project_and_baseline(db_session)
    repository = ChangeRequestRepository(db_session)
    await _create(repository, project, baseline, 1)

    with pytest.raises(IntegrityError):
        await _create(repository, project, baseline, 1)
