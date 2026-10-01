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


def function_list_model(*, group: str = "reservations") -> dict:
    """段階1の検証を通る最小の機能一覧(処理1件・機能グループ1つ)。`group`を一覧に無い名前に
    すると、検証のエラー(UNKNOWN_GROUP)になる。"""
    return {
        "groups": ["reservations"],
        "functions": [
            {
                "id": "F-01",
                "name": "予約を登録する",
                "kind": "API",
                "trigger": "POST /api/v1/reservations",
                "screens": ["SCR-001"],
                "group_initial": "reservations",
                "group": group,
                "summary": "予約を保存する",
            }
        ],
        "next_number": 2,
    }
