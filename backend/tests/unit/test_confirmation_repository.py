"""確認事項の利用者が決めた欄の表とリポジトリのテスト。

SUT: ConfirmationRepository(list_for_project / save / mark_confirmed)と、ConfirmationItemRecord
     の ORM 定義(Project の relationship を含む)
ドライバ: pytest のテスト関数
スタブ不要 ── 永続化そのものが検証対象のため、テスト用の SQLite のセッションを使う(外部キーの
CASCADE は SQLite では効かないので、プロジェクトの削除で消えることは統合テストで確かめる)
"""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.confirmations import ConfirmationItem
from app.models.confirmation import ConfirmationItemRecord
from app.models.project import Project
from app.models.user import User
from app.repositories.confirmation import ConfirmationRepository

_ITEM = ConfirmationItem("req:FR-001", "requirement", "FR-001 記事を集める")


async def _create_project(session: AsyncSession) -> tuple[Project, User]:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="detailed")
    session.add(project)
    await session.flush()
    return project, user


async def test_save_creates_then_updates_only_given_fields(db_session: AsyncSession) -> None:
    project, user = await _create_project(db_session)
    repo = ConfirmationRepository(db_session)
    created = await repo.save(project.id, _ITEM, opportunity="email", updated_by=user.id)
    assert (created.status, created.opportunity, created.memo) == ("unconfirmed", "email", "")
    assert created.kind == "requirement"
    assert created.updated_by == user.id

    renamed = ConfirmationItem("req:FR-001", "requirement", "FR-001 記事を毎朝集める")
    updated = await repo.save(project.id, renamed, status="rejected", memo="不要")
    assert updated.id == created.id
    assert (updated.status, updated.opportunity, updated.memo) == ("rejected", "email", "不要")
    assert updated.label == "FR-001 記事を毎朝集める"

    cleared = await repo.save(project.id, renamed, opportunity=None)
    assert cleared.opportunity is None
    assert cleared.status == "rejected"


async def test_mark_confirmed_keeps_opportunity_and_memo(db_session: AsyncSession) -> None:
    project, user = await _create_project(db_session)
    repo = ConfirmationRepository(db_session)
    await repo.save(project.id, _ITEM, opportunity="next_meeting", memo="聞く")
    record = await repo.mark_confirmed(project.id, _ITEM, updated_by=user.id)
    assert (record.status, record.opportunity, record.memo) == ("confirmed", "next_meeting", "聞く")


async def test_list_for_project_is_per_project(db_session: AsyncSession) -> None:
    project, _ = await _create_project(db_session)
    other, _ = await _create_project(db_session)
    repo = ConfirmationRepository(db_session)
    await repo.save(project.id, _ITEM, status="confirmed")
    await repo.save(project.id, ConfirmationItem("point:core", "point", "中心の機能"))
    await repo.save(other.id, _ITEM)
    keys = [r.key for r in await repo.list_for_project(project.id)]
    assert sorted(keys) == ["point:core", "req:FR-001"]
    await db_session.refresh(project, ["confirmation_items"])
    assert len(project.confirmation_items) == 2


async def test_key_is_unique_per_project(db_session: AsyncSession) -> None:
    project, _ = await _create_project(db_session)
    for _ in range(2):
        db_session.add(
            ConfirmationItemRecord(
                project_id=project.id, key="req:FR-001", kind="requirement", label="x"
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()
