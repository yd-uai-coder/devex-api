import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM

from app.models.chat_history import ChatHistory
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import DOC_TYPES, GeneratedDocumentRepository
from app.services.doc_generator_service import DocGeneratorService, _render_transcript


async def _create_project(session: AsyncSession) -> Project:
    user = User(email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    await ChatHistoryRepository(session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    return project


def _fake_llm_for_generation() -> FakeLLM:
    # DOC_TYPES(4件)の生成 + 自己診断(1件) = 計5回のainvoke呼び出しを想定した順序のcontent。
    contents: list[str | list[str | dict[Any, Any]]] = [f"# {doc_type}\n内容" for doc_type in DOC_TYPES]
    contents.append("## 自己診断\n最重要: 特になし")
    return FakeLLM(content_sequence=contents)


async def test_generate_creates_all_four_documents(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    documents = await GeneratedDocumentRepository(db_session).list_latest_for_project(project.id)
    assert {d.doc_type for d in documents} == set(DOC_TYPES)
    assert all(d.version == 1 for d in documents)


async def test_generate_records_self_diagnosis_as_others_chat_history(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    others = [h for h in history if h.sender == "others"]
    assert len(others) == 1
    assert "自己診断" in others[0].message


async def test_generate_transitions_status_to_completed(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    fake_llm = _fake_llm_for_generation()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    await db_session.refresh(project)
    assert project.status == "completed"


async def test_generate_reverts_status_and_records_failure_on_llm_error(
    db_session: AsyncSession,
) -> None:
    class _RaisingLLM:
        async def ainvoke(self, _messages: list) -> object:
            raise RuntimeError("quota exceeded")

    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=_RaisingLLM())  # 例外を送出しないことを確認

    await db_session.refresh(project)
    assert project.status == "interviewing"
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    others = [h for h in history if h.sender == "others"]
    assert len(others) == 1
    assert "生成に失敗しました" in others[0].message


async def test_generate_reverts_to_revising_when_regeneration_fails(
    db_session: AsyncSession,
) -> None:
    """revising(修正中)から再生成を試みて失敗した場合、interviewingではなくrevisingへ
    戻すことを確認する(生成済みだったという文脈を失わないため)。"""

    class _RaisingLLM:
        async def ainvoke(self, _messages: list) -> object:
            raise RuntimeError("quota exceeded")

    project = await _create_project(db_session)
    project.status = "revising"
    await db_session.commit()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=_RaisingLLM())

    await db_session.refresh(project)
    assert project.status == "revising"


async def test_generate_from_revising_transitions_to_completed_on_success(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    project.status = "revising"
    await db_session.commit()
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=_fake_llm_for_generation())

    await db_session.refresh(project)
    assert project.status == "completed"


async def test_generate_on_missing_project_does_nothing(db_session: AsyncSession) -> None:
    service = DocGeneratorService(db_session)
    fake_llm = _fake_llm_for_generation()

    await service.generate(uuid.uuid4(), uuid.uuid4(), llm=fake_llm)  # 例外を送出しないことを確認

    assert fake_llm.invoke_messages == []  # LLMは一度も呼ばれない


async def test_generate_twice_adds_new_version_not_overwrite(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    service = DocGeneratorService(db_session)
    await service.generate(project.id, project.user_id, llm=_fake_llm_for_generation())

    await service.generate(project.id, project.user_id, llm=_fake_llm_for_generation())

    documents = await GeneratedDocumentRepository(db_session).list_latest_for_project(project.id)
    assert all(d.version == 2 for d in documents)


async def test_generate_extracts_text_from_thought_signature_content(
    db_session: AsyncSession,
) -> None:
    """Geminiがthought signature付きの応答(contentが辞書のリスト)を返しても、
    生成された文書・自己診断結果がrepr化されずプレーンテキストで保存されることを確認する。"""
    project = await _create_project(db_session)
    contents: list[str | list[str | dict[Any, Any]]] = [
        [{"type": "text", "text": f"# {doc_type}\n内容", "extras": {"signature": "sig"}}]
        for doc_type in DOC_TYPES
    ]
    contents.append([{"type": "text", "text": "## 自己診断\n最重要: 特になし"}])
    fake_llm = FakeLLM(content_sequence=contents)
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    documents = await GeneratedDocumentRepository(db_session).list_latest_for_project(project.id)
    for document in documents:
        assert "{'type'" not in document.content
        assert "内容" in document.content
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    others = [h for h in history if h.sender == "others"]
    assert "{'type'" not in others[0].message
    assert "自己診断" in others[0].message


async def test_generate_chains_each_document_from_prior_confirmed_documents(
    db_session: AsyncSession,
) -> None:
    """requirementsのみチャット全履歴を直接読み、それ以外は前段で確定済みの文書だけを
    入力にする(連鎖構成)ことを、実際にLLMへ渡されたHumanMessageの中身で確認する。
    external_designはrequirementsのみ、internal_designはrequirements+external_design、
    implementation_planはrequirements+internal_design(external_designは含めない)を
    入力にする、という参照関係を検証する。"""
    project = await _create_project(db_session)
    contents = [
        "REQUIREMENTS_マーカー",
        "EXTERNAL_DESIGN_マーカー",
        "INTERNAL_DESIGN_マーカー",
        "IMPLEMENTATION_PLAN_マーカー",
        "自己診断結果",
    ]
    fake_llm = FakeLLM(content_sequence=list(contents))
    service = DocGeneratorService(db_session)

    await service.generate(project.id, project.user_id, llm=fake_llm)

    human_contents = [messages[-1].content for messages in fake_llm.invoke_messages]

    # requirements: チャット全履歴(transcript)を直接読む
    assert "備品予約システムを作りたい" in human_contents[0]

    # external_design: requirementsの生成結果のみ
    assert "REQUIREMENTS_マーカー" in human_contents[1]
    assert "EXTERNAL_DESIGN_マーカー" not in human_contents[1]

    # internal_design: requirements + external_designの生成結果
    assert "REQUIREMENTS_マーカー" in human_contents[2]
    assert "EXTERNAL_DESIGN_マーカー" in human_contents[2]

    # implementation_plan: requirements + internal_designの生成結果(external_designは含めない)
    assert "REQUIREMENTS_マーカー" in human_contents[3]
    assert "INTERNAL_DESIGN_マーカー" in human_contents[3]
    assert "EXTERNAL_DESIGN_マーカー" not in human_contents[3]


def test_render_transcript_excludes_others_sender() -> None:
    history = [
        ChatHistory(sender="user", message="要望A"),
        ChatHistory(sender="ai", message="応答A"),
        ChatHistory(sender="others", message="自己診断結果"),
    ]

    transcript = _render_transcript(history)

    assert "要望A" in transcript
    assert "応答A" in transcript
    assert "自己診断結果" not in transcript
