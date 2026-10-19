"""合意済みの版(束)の表とリポジトリのテスト。

SUT: BaselineRepository(next_number / create / list_for_project / get_by_number)と、
     Baseline・BaselineItem の ORM 定義
ドライバ: pytest のテスト関数
スタブ不要 ── 永続化そのものが検証対象のため、テスト用の SQLite のセッションを使う(外部キーの
CASCADE は SQLite では効かないので、プロジェクトの削除で消えることは統合テストで確かめる)
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.baseline import Baseline, BaselineItem
from app.models.project import Project
from app.models.user import User
from app.repositories.baseline import BaselineRepository, ItemSnapshot

_AGREED_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="detailed")
    session.add(project)
    await session.flush()
    return project


def _items() -> list[ItemSnapshot]:
    return [
        ItemSnapshot(
            key="requirements",
            included=True,
            state="present",
            source_version=2,
            content="# r",
            model={"rows": []},
            generated_model="m-1",
        ),
        ItemSnapshot(key="stage:1", included=False, state="not_started"),
    ]


async def test_numbers_are_sequential_per_project(db_session: AsyncSession) -> None:
    repository = BaselineRepository(db_session)
    first = await _create_project(db_session)
    second = await _create_project(db_session)

    assert await repository.next_number(first.id) == 1
    await repository.create(
        project_id=first.id, number=1, agreed_at=_AGREED_AT, reason="要件の合意", items=_items()
    )

    assert await repository.next_number(first.id) == 2
    assert await repository.next_number(second.id) == 1


async def test_create_keeps_record_and_items_in_order(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repository = BaselineRepository(db_session)

    created = await repository.create(
        project_id=project.id,
        number=1,
        agreed_at=_AGREED_AT,
        reason="要件の合意",
        items=_items(),
        counterpart="山田様",
        memo="定例で確認",
        recorded_by=project.user_id,
    )
    await db_session.commit()
    db_session.expunge_all()

    loaded = await repository.get_by_number(project_id=project.id, number=1)
    assert loaded is not None and loaded.id == created.id
    assert isinstance(loaded, Baseline)
    assert (loaded.counterpart, loaded.memo, loaded.recorded_by) == (
        "山田様",
        "定例で確認",
        project.user_id,
    )
    assert [item.key for item in loaded.items] == ["requirements", "stage:1"]
    requirements: BaselineItem = loaded.items[0]
    assert (requirements.source_version, requirements.content, requirements.model) == (
        2,
        "# r",
        {"rows": []},
    )
    assert requirements.generated_model == "m-1"
    assert loaded.items[1].included is False
    assert loaded.items[1].model is None


async def test_list_is_newest_first_and_missing_number_is_none(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repository = BaselineRepository(db_session)
    for number in (1, 2):
        await repository.create(
            project_id=project.id, number=number, agreed_at=_AGREED_AT, reason="r", items=[]
        )

    listed = await repository.list_for_project(project.id)

    assert [b.number for b in listed] == [2, 1]
    assert await repository.get_by_number(project_id=project.id, number=3) is None


async def test_duplicate_number_is_rejected_by_db(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repository = BaselineRepository(db_session)
    await repository.create(
        project_id=project.id, number=1, agreed_at=_AGREED_AT, reason="r", items=[]
    )

    with pytest.raises(IntegrityError):
        await repository.create(
            project_id=project.id, number=1, agreed_at=_AGREED_AT, reason="r", items=[]
        )
