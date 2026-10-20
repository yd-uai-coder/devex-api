"""合意の記録の表とリポジトリのテスト。

SUT: AgreementRepository(append / list_for_project / current)と、AgreementRecord の ORM 定義
ドライバ: pytest のテスト関数
スタブ不要 ── 永続化そのものが検証対象のため、テスト用の SQLite のセッションを使う(外部キーの
CASCADE は SQLite では効かないので、プロジェクトの削除で消えることは統合テストで確かめる)
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agreements import AgreedItem
from app.models.agreement import AgreementRecord
from app.models.baseline import Baseline
from app.models.project import Project
from app.models.user import User
from app.repositories.agreement import AgreementRepository


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="detailed")
    session.add(project)
    await session.flush()
    return project


def _item(key: str, text: str) -> AgreedItem:
    return AgreedItem(key, "requirement", f"{key} {text}", {"id": key[4:], "text": text})


async def _baseline(session: AsyncSession, project: Project, number: int) -> Baseline:
    baseline = Baseline(
        project_id=project.id,
        number=number,
        agreed_at=datetime(2026, 10, 20, tzinfo=UTC),
        reason="合意",
    )
    session.add(baseline)
    await session.flush()
    return baseline


async def test_append_numbers_records_in_order(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    baseline = await _baseline(db_session, project, 1)
    repo = AgreementRepository(db_session)
    records = await repo.append(
        project_id=project.id,
        items=[_item("req:FR-001", "a"), _item("req:FR-002", "b")],
        origin="baseline",
        baseline_id=baseline.id,
    )
    assert [r.seq for r in records] == [1, 2]
    assert {r.created_at for r in records} == {records[0].created_at}
    assert records[0].content == {"id": "FR-001", "text": "a"}
    adopted = await repo.append(
        project_id=project.id, items=[_item("req:FR-002", "b")], origin="adopt"
    )
    assert adopted[0].seq == 3
    assert adopted[0].baseline_id is None


async def test_current_takes_latest_per_key_and_drops_removed(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    other = await _create_project(db_session)
    b1 = await _baseline(db_session, project, 1)
    b2 = await _baseline(db_session, project, 2)
    repo = AgreementRepository(db_session)
    first = await repo.append(
        project_id=project.id,
        items=[_item("req:FR-001", "a"), _item("req:FR-002", "b")],
        origin="baseline",
        baseline_id=b1.id,
    )
    await repo.append(project_id=other.id, items=[_item("req:FR-009", "x")], origin="adopt")
    # B-2: FR-001 を変え、FR-002 は外れた
    second = await repo.append(
        project_id=project.id,
        items=[_item("req:FR-001", "a2")],
        origin="baseline",
        baseline_id=b2.id,
        removed=[first[1]],
    )
    assert second[1].content is None
    removed = second[1]
    assert (removed.key, removed.kind) == ("req:FR-002", "requirement")
    assert removed.label == "req:FR-002 b"

    current = await repo.current(project.id)
    assert [(r.key, r.content) for r in current] == [("req:FR-001", {"id": "FR-001", "text": "a2"})]
    assert current[0].baseline is not None and current[0].baseline.number == 2
    history = await repo.list_for_project(project.id)
    assert [r.seq for r in history] == [1, 2, 3, 4]
    assert all(isinstance(r, AgreementRecord) for r in history)


async def test_seq_is_unique_per_project(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    db_session.add_all(
        [
            AgreementRecord(
                project_id=project.id, seq=1, key=k, kind="requirement", label=k, origin="adopt"
            )
            for k in ("req:FR-001", "req:FR-002")
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
