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


async def test_list_current_for_project_returns_one_per_doc_type(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v2")
    await repo.create_version(project_id=project.id, doc_type="external_design", content="d1")

    result = await repo.list_current_for_project(project.id)

    contents = {d.doc_type: d.content for d in result}
    assert contents == {"requirements": "v2", "external_design": "d1"}


async def test_list_versions_returns_newest_first(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    for content in ("v1", "v2", "v3"):
        await repo.create_version(project_id=project.id, doc_type="requirements", content=content)

    versions = await repo.list_versions(project_id=project.id, doc_type="requirements")

    assert [v.content for v in versions] == ["v3", "v2", "v1"]


async def test_get_version_returns_specific_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v2")

    version1 = await repo.get_version(project_id=project.id, doc_type="requirements", version=1)

    assert version1 is not None
    assert version1.content == "v1"


async def test_get_version_returns_none_for_missing_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)

    result = await repo.get_version(project_id=project.id, doc_type="requirements", version=99)

    assert result is None


async def test_create_version_marks_only_newest_as_current(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    for content in ("v1", "v2", "v3"):
        await repo.create_version(project_id=project.id, doc_type="requirements", content=content)

    versions = await repo.list_versions(project_id=project.id, doc_type="requirements")

    assert [(v.version, v.is_current) for v in versions] == [(3, True), (2, False), (1, False)]


async def test_create_version_keeps_current_flag_valid_after_pruning(
    db_session: AsyncSession,
) -> None:
    """保持3件超過でv1が削除されても、currentは新しいv4ちょうど1件のまま。"""
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    for content in ("v1", "v2", "v3", "v4"):
        await repo.create_version(project_id=project.id, doc_type="requirements", content=content)

    versions = await repo.list_versions(project_id=project.id, doc_type="requirements")

    assert [(v.version, v.is_current) for v in versions] == [(4, True), (3, False), (2, False)]


async def test_set_current_switches_flag_without_adding_rows(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    for content in ("v1", "v2"):
        await repo.create_version(project_id=project.id, doc_type="requirements", content=content)

    target = await repo.set_current(project_id=project.id, doc_type="requirements", version=1)

    assert target is not None and target.version == 1 and target.is_current is True
    versions = await repo.list_versions(project_id=project.id, doc_type="requirements")
    assert [(v.version, v.is_current) for v in versions] == [(2, False), (1, True)]


async def test_set_current_returns_none_and_changes_nothing_for_missing_version(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")

    result = await repo.set_current(project_id=project.id, doc_type="requirements", version=99)

    assert result is None
    current = await repo.get_current(project_id=project.id, doc_type="requirements")
    assert current is not None and current.version == 1


async def test_get_current_is_independent_per_doc_type(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="r1")
    await repo.create_version(project_id=project.id, doc_type="requirements", content="r2")
    await repo.create_version(project_id=project.id, doc_type="external_design", content="d1")
    await repo.set_current(project_id=project.id, doc_type="requirements", version=1)

    requirements = await repo.get_current(project_id=project.id, doc_type="requirements")
    design = await repo.get_current(project_id=project.id, doc_type="external_design")

    assert requirements is not None and requirements.content == "r1"
    assert design is not None and design.content == "d1"  # 他doc_typeのcurrentは影響を受けない


async def test_update_content_in_place_keeps_version_and_other_versions(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="internal_design", content="v1")
    current = await repo.create_version(
        project_id=project.id, doc_type="internal_design", content="v2"
    )

    await repo.update_content_in_place(current, "v2 + 図")

    versions = await repo.list_versions(project_id=project.id, doc_type="internal_design")
    assert [(v.version, v.content) for v in versions] == [(2, "v2 + 図"), (1, "v1")]
    assert versions[0].is_current
