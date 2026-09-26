from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository


async def _create_project(session: AsyncSession) -> Project:
    user = User(email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    return project


async def test_create_version_starts_at_1(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)

    doc = await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")

    assert doc.version == 1


async def test_create_version_increments_and_does_not_overwrite(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")

    second = await repo.create_version(project_id=project.id, doc_type="requirements", content="v2")

    assert second.version == 2
    latest = await repo.get_latest(project_id=project.id, doc_type="requirements")
    assert latest is not None
    assert latest.content == "v2"  # 最新版がv2になっている(上書きではなく新バージョン追加)


async def test_create_version_prunes_oldest_beyond_three(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    for content in ("v1", "v2", "v3"):
        await repo.create_version(project_id=project.id, doc_type="requirements", content=content)

    await repo.create_version(project_id=project.id, doc_type="requirements", content="v4")

    remaining = await repo.list_all(
        order_by=repo.model.version.asc(), project_id=project.id, doc_type="requirements"
    )
    assert [d.content for d in remaining] == ["v2", "v3", "v4"]  # v1(最古)が削除され3件のみ残る


async def test_create_version_is_independent_per_doc_type(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)

    req = await repo.create_version(project_id=project.id, doc_type="requirements", content="r1")
    design = await repo.create_version(
        project_id=project.id, doc_type="external_design", content="d1"
    )

    assert req.version == 1
    assert design.version == 1


async def test_list_latest_for_project_returns_one_per_doc_type(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v2")
    await repo.create_version(project_id=project.id, doc_type="external_design", content="d1")

    result = await repo.list_latest_for_project(project.id)

    contents = {d.doc_type: d.content for d in result}
    assert contents == {"requirements": "v2", "external_design": "d1"}
