import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.projects import (
    list_document_versions,
    list_generated_documents,
    restore_document_version,
)
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository
from app.services.errors import DocumentNotFoundError


async def _create_project(session: AsyncSession, title: str = "備品予約") -> Project:
    user = User(email=f"{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title=title)
    session.add(project)
    await session.flush()
    return project


async def test_list_document_versions_returns_newest_first(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    for content in ("v1", "v2"):
        await repo.create_version(project_id=project.id, doc_type="requirements", content=content)

    result = await list_document_versions("requirements", db_session, project)

    assert [d.content for d in result] == ["v2", "v1"]


async def test_restore_document_version_switches_current_without_new_version(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v2")

    restored = await restore_document_version("requirements", 1, db_session, project)

    assert restored.version == 1
    assert restored.content == "v1"
    assert restored.is_current is True
    versions = await list_document_versions("requirements", db_session, project)
    assert [(v.version, v.is_current) for v in versions] == [(2, False), (1, True)]


async def test_restore_same_version_repeatedly_does_not_increase_versions(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v2")

    for _ in range(3):
        await restore_document_version("requirements", 1, db_session, project)

    versions = await list_document_versions("requirements", db_session, project)
    assert [v.version for v in versions] == [2, 1]


async def test_list_generated_documents_returns_current_version(db_session: AsyncSession) -> None:
    """一覧(ドキュメントプレビュー画面のデータ源)は最新版ではなく表示中の版を返す。"""
    project = await _create_project(db_session)
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v1")
    await repo.create_version(project_id=project.id, doc_type="requirements", content="v2")
    await restore_document_version("requirements", 1, db_session, project)

    documents = await list_generated_documents(db_session, project)

    assert [(d.doc_type, d.content, d.is_current) for d in documents] == [
        ("requirements", "v1", True)
    ]


async def test_restore_document_version_rejects_missing_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)

    with pytest.raises(DocumentNotFoundError):
        await restore_document_version("requirements", 99, db_session, project)
