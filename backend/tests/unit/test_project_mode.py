import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM

from app.api.routes.projects import create_project
from app.models.project import Project
from app.models.prompt_template import PromptTemplate
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import DOC_TYPES, GeneratedDocumentRepository
from app.repositories.project import ProjectRepository
from app.schemas.project import ProjectRead
from app.services.chat_service import ChatService
from app.services.doc_generator_service import (
    _DOC_TYPE_INPUTS,
    _DOC_TYPE_PROMPTS,
    DOC_TYPES_BY_MODE,
    DocGeneratorService,
)
from app.services.project import ProjectService


async def _create_user(session: AsyncSession) -> User:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    return user


async def _create_project(session: AsyncSession, *, mode: str) -> Project:
    user = await _create_user(session)
    project = await ProjectRepository(session).create(user_id=user.id, title="p", mode=mode)
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    return project


async def test_create_project_route_passes_template_id_and_mode(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ルートが template_id をサービスへ渡すこと。あわせて mode も渡す。"""

    async def _no_opening_reply(self: ChatService, project: Project) -> None:
        return None

    monkeypatch.setattr(ChatService, "generate_opening_reply", _no_opening_reply)
    user = await _create_user(db_session)
    template = PromptTemplate(name="Web標準", target_type="Web", system_prompt="x")
    db_session.add(template)
    await db_session.flush()

    result = await create_project(
        db_session,
        user,
        system_overview="備品予約",
        goals_raw="重複を防ぐ",
        template_id=template.id,
        mode="detailed",
        files=[],
    )

    assert isinstance(result, ProjectRead)
    assert result.mode == "detailed"
    saved = await ProjectRepository(db_session).get_by_id(result.id, user_id=user.id)
    assert saved is not None
    assert saved.template_id == template.id


async def test_service_create_defaults_mode_to_simple(db_session: AsyncSession) -> None:
    user = await _create_user(db_session)

    project = await ProjectService(db_session).create(
        user_id=user.id, intake={"system_overview": "s"}, files=[]
    )

    assert project.mode == "simple"
    detail = await ProjectService(db_session).get_detail(project)
    assert detail.mode == "simple"


def test_doc_types_by_mode_keep_generation_order() -> None:
    """どのモードでも、ある文書の入力(_DOC_TYPE_INPUTS)は、その文書より前に生成される。"""
    assert DOC_TYPES_BY_MODE["simple"] == DOC_TYPES
    assert DOC_TYPES_BY_MODE["detailed"] == ("requirements", "external_design")
    for doc_types in DOC_TYPES_BY_MODE.values():
        for index, doc_type in enumerate(doc_types):
            assert set(_DOC_TYPE_INPUTS.get(doc_type, ())) <= set(doc_types[:index])


async def test_generate_in_detailed_mode_creates_only_two_documents(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session, mode="detailed")
    # 要件定義・外部設計(2件) + 自己診断(1件) = 計3回の呼び出し
    fake_llm = FakeLLM(content_sequence=["# 要件", "# 外部", "## 自己診断\n特になし"])

    await DocGeneratorService(db_session).generate(project.id, project.user_id, llm=fake_llm)

    documents = await GeneratedDocumentRepository(db_session).list_current_for_project(project.id)
    assert [d.doc_type for d in documents] == ["requirements", "external_design"]
    await db_session.refresh(project)
    assert project.status == "completed"


def test_internal_design_prompt_asks_for_module_list_table() -> None:
    """簡易ドキュメントモードの内部設計書にも、段階4と同じ列(関わる処理を除く)の表を求める。"""
    prompt = _DOC_TYPE_PROMPTS["internal_design"]

    assert "### モジュール一覧" in prompt
    assert "(パス/層/責務/主な依存先)" in prompt
