import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM

from app.api.routes.projects import create_project, get_hearing_completion
from app.models.chat_history import ChatHistory
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.schemas.generation import HearingCompletionCheck
from app.services.chat_service import ChatService, _build_messages


async def _create_project(session: AsyncSession) -> Project:
    user = User(email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    return project


async def test_stream_reply_persists_user_and_ai_messages(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    fake_llm = FakeLLM(stream_chunks=["こん", "にちは"])
    service = ChatService(db_session)

    chunks = [
        chunk
        async for chunk in service.stream_reply(project, user_message="はじめまして", llm=fake_llm)
    ]

    assert chunks == ["こん", "にちは"]
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert [(h.sender, h.message) for h in history] == [
        ("user", "はじめまして"),
        ("ai", "こんにちは"),
    ]


async def test_stream_reply_transitions_completed_project_to_revising(
    db_session: AsyncSession,
) -> None:
    user = User(email="owner-revise@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    project = Project(user_id=user.id, title="p", status="completed")
    db_session.add(project)
    await db_session.flush()
    fake_llm = FakeLLM(stream_chunks=["承知しました"])
    service = ChatService(db_session)

    async for _ in service.stream_reply(project, user_message="ここを直したい", llm=fake_llm):
        pass

    assert project.status == "revising"


async def test_stream_reply_does_not_change_status_when_already_interviewing_or_revising(
    db_session: AsyncSession,
) -> None:
    for status in ("interviewing", "revising"):
        user = User(email=f"owner-{status}@example.com", hashed_password="x")
        db_session.add(user)
        await db_session.flush()
        project = Project(user_id=user.id, title="p", status=status)
        db_session.add(project)
        await db_session.flush()
        fake_llm = FakeLLM(stream_chunks=["了解"])
        service = ChatService(db_session)

        async for _ in service.stream_reply(project, user_message="続き", llm=fake_llm):
            pass

        assert project.status == status


async def test_stream_reply_extracts_text_from_thought_signature_content(
    db_session: AsyncSession,
) -> None:
    """Geminiがthought signature付きの応答(contentが辞書のリスト)を返しても、
    text以外のメタデータ(extras/signature)を含めずプレーンテキストとして
    ストリーミング・永続化されることを確認する。"""
    project = await _create_project(db_session)
    fake_llm = FakeLLM(
        stream_chunks=[
            "通常の",
            [{"type": "text", "text": "テキスト", "extras": {"signature": "sig"}}],
        ]
    )
    service = ChatService(db_session)

    chunks = [
        chunk
        async for chunk in service.stream_reply(project, user_message="こんにちは", llm=fake_llm)
    ]

    assert chunks == ["通常の", "テキスト"]
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert [(h.sender, h.message) for h in history] == [
        ("user", "こんにちは"),
        ("ai", "通常のテキスト"),
    ]


async def test_stream_reply_skips_signature_only_chunks(db_session: AsyncSession) -> None:
    """textを持たずsignatureのみのチャンク(空文字列に変換される)はyieldされないことを確認する。"""
    project = await _create_project(db_session)
    fake_llm = FakeLLM(
        stream_chunks=[
            [{"type": "text", "text": "", "extras": {"signature": "sig"}}],
            "本文",
        ]
    )
    service = ChatService(db_session)

    chunks = [
        chunk
        async for chunk in service.stream_reply(project, user_message="こんにちは", llm=fake_llm)
    ]

    assert chunks == ["本文"]


async def test_check_completion_returns_structured_result(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    await ChatHistoryRepository(db_session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    expected = HearingCompletionCheck(
        is_sufficient=True, summary="備品予約システムの要件が整理できました。", missing_points=[]
    )
    fake_llm = FakeLLM(structured=expected)
    service = ChatService(db_session)

    result = await service.check_completion(project, llm=fake_llm)

    assert result == expected
    assert fake_llm.structured_output_calls == [HearingCompletionCheck]

async def test_get_hearing_completion_route_delegates_to_chat_service(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /projects/{id}/hearing-completion がChatService.check_completionへ
    正しく委譲されることを確認する(ルート自体はllmを注入できないため、get_gemini_llmを
    モンキーパッチしてFakeLLMに差し替える。test_ai_graph_nodes.pyと同じ手法)。"""
    project = await _create_project(db_session)
    await ChatHistoryRepository(db_session).add(
        project_id=project.id, sender="user", message="備品予約システムを作りたい"
    )
    expected = HearingCompletionCheck(
        is_sufficient=True, summary="備品予約システムの要件が整理できました。", missing_points=[]
    )
    monkeypatch.setattr(
        "app.services.chat_service.get_gemini_llm", lambda: FakeLLM(structured=expected)
    )

    result = await get_hearing_completion(db_session, project)

    assert result == expected


async def test_create_project_route_generates_opening_ai_reply(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /projects (create_project)が、intake保存後にChatService.generate_opening_replyを
    呼び、AIの最初の発話がchat_historiesに保存されることを確認する。"""
    user = User(email="owner2@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    monkeypatch.setattr(
        "app.services.chat_service.get_gemini_llm",
        lambda: FakeLLM(
            content="【確認したい事】\n・想定ユーザー\n\nまず、対象ユーザーを教えてください。"
        ),
    )

    project_read = await create_project(
        session=db_session,
        current_user=user,
        system_overview="トレンド情報を調べたい",
        goals_raw="効率的に把握したい",
        files=[],
    )

    history = await ChatHistoryRepository(db_session).list_for_project(project_read.id)
    assert [h.sender for h in history] == ["intake", "ai"]
    assert "確認したい事" in history[1].message


async def test_create_project_route_succeeds_even_if_opening_reply_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AIの最初の発話生成がクォータ超過等で失敗しても、プロジェクト作成自体は成功することを確認する。"""
    user = User(email="owner3@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()

    # _is_quota_errorをTrueに固定し、invoke_with_retryがリトライせず即座に
    # LLMQuotaExceededErrorを送出するようにする(test_llm_retry.pyと同じ手法)。
    import app.services.llm_retry as llm_retry_module

    monkeypatch.setattr(llm_retry_module, "_is_quota_error", lambda _exc: True)

    class _FailingLLM(FakeLLM):
        async def ainvoke(self, messages):  # noqa: ANN001
            raise RuntimeError("quota exceeded")

    monkeypatch.setattr("app.services.chat_service.get_gemini_llm", lambda: _FailingLLM())

    project_read = await create_project(
        session=db_session,
        current_user=user,
        system_overview="トレンド情報を調べたい",
        goals_raw="効率的に把握したい",
        files=[],
    )

    history = await ChatHistoryRepository(db_session).list_for_project(project_read.id)
    assert [h.sender for h in history] == ["intake"]

def _entry(sender: str, message: str) -> ChatHistory:
    return ChatHistory(sender=sender, message=message)


def _project(intake: dict | None = None) -> Project:
    return Project(intake=intake)


def test_build_messages_maps_user_and_intake_to_human_message() -> None:
    history = [_entry("intake", "概要: 備品予約"), _entry("user", "予約の重複を防ぎたい")]

    messages = _build_messages(history, _project())

    assert isinstance(messages[0], SystemMessage)
    assert messages[1:] == [
        HumanMessage(content="概要: 備品予約"),
        HumanMessage(content="予約の重複を防ぎたい"),
    ]


def test_build_messages_maps_attachment_to_human_message() -> None:
    # sender='attachment'(添付ファイル抽出結果)はチャット画面には表示しないが、
    # ヒアリング対話のLLMコンテキストには'user'/'intake'と同様に含める
    history = [_entry("attachment", "[添付ファイル: spec.pdf]\n本文")]

    messages = _build_messages(history, _project())

    assert messages[1:] == [HumanMessage(content="[添付ファイル: spec.pdf]\n本文")]


def test_build_messages_maps_ai_to_ai_message() -> None:
    history = [_entry("ai", "承知しました")]

    messages = _build_messages(history, _project())

    assert messages[1:] == [AIMessage(content="承知しました")]


def test_build_messages_excludes_others_sender() -> None:
    # sender='others'(自己診断結果)はヒアリング中の対話文脈には含めない
    history = [_entry("user", "こんにちは"), _entry("others", "自己診断: 不足あり")]

    messages = _build_messages(history, _project())

    assert messages[1:] == [HumanMessage(content="こんにちは")]


def test_build_messages_injects_environment_from_project_intake() -> None:
    # environmentはchat_historiesには保存されない(表示対象外)ため、project.intakeから
    # 直接読み取ってHumanMessageとして注入されることを確認する。
    project = _project(intake={"environment": {"languages": ["Python"]}})

    messages = _build_messages([], project)

    assert len(messages) == 2
    assert isinstance(messages[1], HumanMessage)
    assert "Python" in messages[1].content


def test_build_messages_omits_environment_message_when_not_provided() -> None:
    project = _project(intake={"system_overview": "s"})

    messages = _build_messages([], project)

    assert len(messages) == 1
    assert isinstance(messages[0], SystemMessage)


async def test_generate_opening_reply_persists_ai_message(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    fake_llm = FakeLLM(content="以上を元に詳細のヒアリングを進めていきます。")
    service = ChatService(db_session)

    reply = await service.generate_opening_reply(project, llm=fake_llm)

    assert reply == "以上を元に詳細のヒアリングを進めていきます。"
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert [(h.sender, h.message) for h in history] == [("ai", reply)]
