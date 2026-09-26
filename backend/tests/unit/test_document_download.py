import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.projects import _content_disposition, download_generated_document
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


async def test_download_returns_markdown_with_expected_filename(db_session: AsyncSession) -> None:
    # 本リポジトリdocs/配下の実ファイル名(requirements.md等)に合わせ、
    # {doc_type}.mdをファイル名とする(project.title・生成日時は含めない)。
    project = await _create_project(db_session)
    doc = await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="requirements", content="# 要件定義\n本文"
    )

    response = await download_generated_document(doc.id, db_session, project)

    assert bytes(response.body).decode("utf-8") == "# 要件定義\n本文"
    assert response.media_type == "text/markdown"
    disposition = response.headers["content-disposition"]
    assert 'filename="requirements.md"' in disposition


async def test_download_rejects_document_from_other_project(db_session: AsyncSession) -> None:
    project_a = await _create_project(db_session)
    project_b = await _create_project(db_session)
    doc = await GeneratedDocumentRepository(db_session).create_version(
        project_id=project_a.id, doc_type="requirements", content="..."
    )

    with pytest.raises(DocumentNotFoundError):
        await download_generated_document(doc.id, db_session, project_b)


async def test_download_rejects_missing_document(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)

    with pytest.raises(DocumentNotFoundError):
        await download_generated_document(uuid.uuid4(), db_session, project)


def test_content_disposition_includes_ascii_fallback_and_utf8_filename() -> None:
    header = _content_disposition("備品予約_requirements_20260101.md")

    assert header.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in header
