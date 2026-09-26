from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.repositories.intake_file import IntakeFileRepository


async def _create_project(session: AsyncSession) -> Project:
    user = User(email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    return project


async def test_create_persists_processed_file(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = IntakeFileRepository(db_session)

    entry = await repo.create(
        project_id=project.id,
        filename="spec.pdf",
        file_type="pdf",
        size_bytes=1024,
        extracted_text="抽出結果",
        status="processed",
    )

    assert entry.status == "processed"
    assert entry.extracted_text == "抽出結果"
    assert entry.error_message is None


async def test_create_persists_failed_file_without_text(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = IntakeFileRepository(db_session)

    entry = await repo.create(
        project_id=project.id,
        filename="broken.pdf",
        file_type="pdf",
        size_bytes=10,
        extracted_text=None,
        status="failed",
        error_message="破損したファイルです",
    )

    assert entry.status == "failed"
    assert entry.extracted_text is None
    assert entry.error_message == "破損したファイルです"


async def test_list_for_project_returns_all_files(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repo = IntakeFileRepository(db_session)
    await repo.create(
        project_id=project.id,
        filename="a.txt",
        file_type="txt",
        size_bytes=10,
        extracted_text="a",
        status="processed",
    )
    await repo.create(
        project_id=project.id,
        filename="b.md",
        file_type="md",
        size_bytes=20,
        extracted_text="b",
        status="processed",
    )

    result = await repo.list_for_project(project.id)

    assert [f.filename for f in result] == ["a.txt", "b.md"]
