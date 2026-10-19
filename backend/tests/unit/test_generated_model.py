"""文書の版に残す、生成したモデル名(`generated_documents.generated_model`)のテスト。

SUT: GeneratedDocumentRepository.create_version、DocGeneratorService.generate(版にモデル名を
     渡す)、GeneratedDocumentRead
ドライバ: pytest のテスト関数
スタブ: FakeLLM(生成の LLM。渡された LLM は `.env` の既定のモデルとして記録される)。DB はテスト用の
     SQLite のセッション
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM

from app.core.config import settings
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import DOC_TYPES, GeneratedDocumentRepository
from app.schemas.document import GeneratedDocumentRead
from app.services.doc_generator_service import DocGeneratorService


async def _create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    return project


async def test_create_version_keeps_generated_model(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    repository = GeneratedDocumentRepository(db_session)

    named = await repository.create_version(
        project_id=project.id, doc_type="requirements", content="# r", generated_model="m-1"
    )
    unnamed = await repository.create_version(
        project_id=project.id, doc_type="external_design", content="# e"
    )

    assert named.generated_model == "m-1"
    assert unnamed.generated_model is None
    assert GeneratedDocumentRead.model_validate(named).generated_model == "m-1"


async def test_generate_records_model_name_on_every_version(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    llm = FakeLLM(
        content_sequence=["## 自己診断\n最重要: 特になし"],
        structured_by_schema=upstream_outputs(),
    )

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=llm)

    documents = await GeneratedDocumentRepository(db_session).list_current_for_project(project.id)
    assert {d.doc_type for d in documents} == set(DOC_TYPES)
    assert {d.generated_model for d in documents} == {settings.GEMINI_MODEL}
