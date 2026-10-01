"""詳細設計モードのテストで使うプロジェクトの組み立て(段階のサービス・ルートのテストで共有する)。"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository


async def create_detailed_project(
    session: AsyncSession, *, mode: str = "detailed", with_documents: bool = True
) -> Project:
    """詳細設計モードのプロジェクトを作る。`with_documents`なら要件定義・外部設計を1版ずつ持つ。"""
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode=mode, status="completed")
    session.add(project)
    await session.flush()
    if with_documents:
        documents = GeneratedDocumentRepository(session)
        for doc_type in ("requirements", "external_design"):
            await documents.create_version(
                project_id=project.id, doc_type=doc_type, content=f"# {doc_type}"
            )
    await session.commit()
    return project
