import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.projects import (
    adopt_requirement,
    list_document_versions,
    list_generated_documents,
    restore_document_version,
)
from app.documents.requirements import RequirementRow, RequirementsModel, render_requirements
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository
from app.services.errors import DocumentNotFoundError, RequirementNotFoundError


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


async def test_requirements_summary_and_adopt_route(db_session: AsyncSession) -> None:
    """要件定義書の応答は出どころの件数・推測の要件・指摘を持ち、「採用にする」で同じ版が変わる。
    ほかの文書・model の無い版は requirements が None。"""
    project = await _create_project(db_session)
    model = RequirementsModel(
        purpose="p",
        rows=[
            RequirementRow(
                id="FR-001", kind="FR", priority="Must", text="予約", source="assumed",
                points=["core"],
            )
        ],
    )
    repo = GeneratedDocumentRepository(db_session)
    await repo.create_version(
        project_id=project.id,
        doc_type="requirements",
        content=render_requirements(model),
        model=model.model_dump(mode="json"),
    )
    await repo.create_version(project_id=project.id, doc_type="external_design", content="e")

    listed = {d.doc_type: d for d in await list_generated_documents(db_session, project)}
    adopted = await adopt_requirement("FR-001", db_session, project)

    summary = listed["requirements"].requirements
    assert summary is not None
    assert summary.counts == {"user": 0, "adopted": 0, "assumed": 1}
    assert [a.id for a in summary.assumed] == ["FR-001"]
    assert "ASSUMED_REQUIREMENTS" in [i.code for i in summary.issues]
    assert listed["external_design"].requirements is None
    assert adopted.version == 1
    assert adopted.requirements is not None
    assert adopted.requirements.counts == {"user": 0, "adopted": 1, "assumed": 0}
    with pytest.raises(RequirementNotFoundError):
        await adopt_requirement("FR-002", db_session, project)
