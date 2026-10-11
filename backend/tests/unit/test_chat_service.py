import pytest
import structlog.testing
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm import FakeLLM

from app.api.routes.projects import (
    create_project,
    get_hearing_completion,
    get_hearing_points,
    update_hearing_point,
)
from app.hearing import (
    POINT_DEFS,
    Card,
    CardOption,
    PointState,
    card_to_dict,
    initial_states,
    is_required,
    point_def,
    scale_card,
)
from app.models.chat_history import ChatHistory
from app.models.project import Project
from app.models.prompt_template import PromptTemplate
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.hearing_point import HearingPointRepository
from app.schemas.generation import HearingCompletionCheck
from app.schemas.hearing import (
    CardAnswer,
    CardOptionOut,
    CardOut,
    HearingMessageRequest,
    HearingPointsJudgement,
    HearingPointUpdate,
    HearingReplyOut,
    JudgedPointOut,
)
from app.services.chat_service import (
    _CODE_CARD_NOTE,
    _EMPTY_REPLY_WITH_CARDS,
    _HEARING_SYSTEM_PROMPT,
    _LATEST_QUESTION_NOTE,
    _MISSING_POINTS_NOTE,
    _NEXT_QUESTION_TEMPLATE,
    _OPENING_SCALE_CARD_LINE,
    _POINTS_JUDGE_PROMPT,
    ChatService,
    ReplyCards,
    _build_messages,
    _drop_completion_claims,
)
from app.services.errors import (
    DocGenerationInProgressError,
    GenerationFailedError,
    HearingPointNotFoundError,
    HearingPointNotOpenError,
    InvalidCardAnswerError,
)

# stream_replyは返信の前に観点の判定を呼ぶ。返信の振る舞いを見るテストでは、何も決まらない
# (必須が未決のまま = 足りない)結果を返させる
_INSUFFICIENT = HearingPointsJudgement()


def _reply_llm(text: str = "こんにちは", **kwargs) -> FakeLLM:  # noqa: ANN003
    """判定は「何も決まらない」、返信は本文`text`(カード無し)を返す偽のLLM。"""
    return FakeLLM(
        structured=_INSUFFICIENT,
        structured_by_schema={HearingReplyOut: HearingReplyOut(message=text)},
        **kwargs,
    )


def _all_decided(evidence: int, *, scale: str = "personal") -> HearingPointsJudgement:
    """全観点を、番号evidenceの発言を根拠に「ユーザーが決定」とする判定の結果。"""
    return HearingPointsJudgement(
        scale=scale,  # type: ignore[arg-type]
        points=[
            JudgedPointOut(
                key=d.key, status="decided", summary=f"{d.label}の決定", evidence=[evidence]
            )
            for d in POINT_DEFS
        ],
    )


async def _create_project(session: AsyncSession) -> Project:
    user = User(email="owner@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p")
    session.add(project)
    await session.flush()
    return project


async def test_list_history_returns_entries_in_chronological_order(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    repo = ChatHistoryRepository(db_session)
    await repo.add(project_id=project.id, sender="user", message="1件目")
    await repo.add(project_id=project.id, sender="ai", message="2件目")
    service = ChatService(db_session)

    history = await service.list_history(project.id)

    assert [h.message for h in history] == ["1件目", "2件目"]


async def test_stream_reply_judges_then_replies_with_structured_output(
    db_session: AsyncSession,
) -> None:
    """返信は構造化出力(本文とカード)の1回で作る。1回の送信でLLMは判定と返信の2回。
    どちらもinvoke_with_retryを通るので、DEBUGログはattempt付きで2件になる(本文は含めない)。"""
    project = await _create_project(db_session)
    fake_llm = _reply_llm()
    service = ChatService(db_session)

    with structlog.testing.capture_logs() as logs:
        async for _ in service.stream_reply(project, user_message="はじめまして", llm=fake_llm):
            pass

    assert fake_llm.structured_output_calls == [HearingPointsJudgement, HearingReplyOut]
    debug_logs = [log for log in logs if log["log_level"] == "debug"]
    assert len(debug_logs) == 2
    assert all("attempt" in log for log in debug_logs)
    assert "はじめまして" not in str(debug_logs)


async def test_stream_reply_persists_user_and_ai_messages(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    fake_llm = _reply_llm()
    service = ChatService(db_session)

    chunks = [
        chunk
        async for chunk in service.stream_reply(project, user_message="はじめまして", llm=fake_llm)
    ]

    # 本文はまとめて1回で返し、その後にカード(規模が未決なので、コードが出す規模のカード)
    assert chunks[0] == "こんにちは"
    assert isinstance(chunks[1], ReplyCards)
    assert [c["point_key"] for c in chunks[1].cards] == ["scale"]
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert [(h.sender, h.message) for h in history] == [
        ("user", "はじめまして"),
        ("ai", "こんにちは"),
    ]


class _RecordingLLM:
    """判定と返信の構造化出力に渡されたmessagesを記録するスタブ(FakeLLMはmessagesを無視するため)。
    返信は本文「こんにちは」と、指定したカードを返す。"""

    def __init__(
        self,
        judgement: HearingPointsJudgement = _INSUFFICIENT,
        *,
        reply: HearingReplyOut | None = None,
    ) -> None:
        self.reply_messages: list[list] = []
        self.judge_messages: list[list] = []
        self._judgement = judgement
        self._reply = reply or HearingReplyOut(message="こんにちは")

    def with_structured_output(self, schema):
        recorder = self

        class _Structured:
            async def ainvoke(self, messages):
                if schema is HearingReplyOut:
                    recorder.reply_messages.append(messages)
                    return recorder._reply
                recorder.judge_messages.append(messages)
                return recorder._judgement

        return _Structured()


async def test_stream_reply_does_not_send_points_judge_prompt(
    db_session: AsyncSession,
) -> None:
    """チャット応答の生成に観点の判定のプロンプトを混ぜない(混ぜるとLLMが判定JSONを返してしまう)。"""
    project = await _create_project(db_session)
    llm = _RecordingLLM()

    async for _ in ChatService(db_session).stream_reply(
        project, user_message="はじめまして", llm=llm
    ):
        pass

    sent = llm.reply_messages[0]
    assert all("観点の表(キー|観点" not in str(m.content) for m in sent)
    assert isinstance(sent[-1], HumanMessage)
    assert sent[-1].content == "はじめまして"


async def test_stream_reply_transitions_completed_project_to_revising(
    db_session: AsyncSession,
) -> None:
    user = User(email="owner-revise@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    project = Project(user_id=user.id, title="p", status="completed")
    db_session.add(project)
    await db_session.flush()
    fake_llm = _reply_llm("承知しました")
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
        fake_llm = _reply_llm("了解")
        service = ChatService(db_session)

        async for _ in service.stream_reply(project, user_message="続き", llm=fake_llm):
            pass

        assert project.status == status


async def _add_history(session: AsyncSession, project: Project, *senders: str) -> list[ChatHistory]:
    repo = ChatHistoryRepository(session)
    return [
        await repo.add(project_id=project.id, sender=sender, message=f"{sender}{i}")
        for i, sender in enumerate(senders)
    ]


async def test_judge_points_saves_states_with_message_ids(db_session: AsyncSession) -> None:
    """LLMが返した発言の番号を、chat_historiesのidに直して保存する(結べない番号は捨てる)。"""
    project = await _create_project(db_session)
    intake, _, user1 = await _add_history(db_session, project, "intake", "ai", "user")
    judgement = HearingPointsJudgement(
        points=[
            JudgedPointOut(key="purpose", status="decided", summary="工数を減らす", evidence=[1]),
            JudgedPointOut(key="users", status="decided", summary="SE", evidence=[2, 2, 9]),
            JudgedPointOut(key="tech", status="decided", summary="推測", evidence=[9]),
        ]
    )
    fake_llm = FakeLLM(structured=judgement)

    states = await ChatService(db_session).judge_points(project, llm=fake_llm)

    assert fake_llm.structured_output_calls == [HearingPointsJudgement]
    assert states["purpose"] == PointState("purpose", "decided", "工数を減らす", (str(intake.id),))
    assert states["users"].evidence == (str(user1.id),)
    # 根拠が利用者側の発言に結べない決定は取り込まない
    assert states["tech"].status == "open"
    rows = {
        r.point_key: r
        for r in await HearingPointRepository(db_session).list_for_project(project.id)
    }
    assert len(rows) == len(POINT_DEFS)
    assert rows["purpose"].evidence == [str(intake.id)]


async def test_judge_points_shows_numbered_messages_and_current_table(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    project.intake = {"environment": {"backend": "FastAPI"}}
    await _add_history(db_session, project, "intake", "ai", "user")
    llm = _RecordingLLM()

    await ChatService(db_session).judge_points(project, llm=llm)

    sent = llm.judge_messages[0]
    human = [str(m.content) for m in sent if isinstance(m, HumanMessage)]
    assert human[0].startswith("[#1] [開発環境の希望]")  # 開発環境の希望は初期入力の番号で見せる
    assert human[1] == "[#1] intake0"
    assert human[2] == "[#2] user2"
    assert all(not str(m.content).startswith("[#") for m in sent if isinstance(m, AIMessage))
    assert human[-1].startswith(_POINTS_JUDGE_PROMPT.split("{table}")[0][:20])
    assert "purpose|目的・課題|" in human[-1]
    assert point_def("tech").done_when in human[-1]  # 決まったと言える条件も表で渡す


async def test_judge_points_names_point_of_latest_ai_card(db_session: AsyncSession) -> None:
    """直前のAIの発言のカードの観点を、判定のプロンプトに添える(最新の答えの取りこぼしを防ぐ)。"""
    project = await _create_project(db_session)
    await _add_history(db_session, project, "intake")
    await _add_ai_with_cards(db_session, project, _availability_card())
    await _add_history(db_session, project, "user")
    llm = _RecordingLLM()

    await ChatService(db_session).judge_points(project, llm=llm)

    prompt = str(llm.judge_messages[0][-1].content)
    assert prompt.endswith(_LATEST_QUESTION_NOTE.format(points="availability(可用性)"))


async def test_judge_points_names_next_point_when_latest_ai_asked_in_text(
    db_session: AsyncSession,
) -> None:
    """カードの無い問いは、前の表から次に聞く観点(返信と同じ計算)を添える。"""
    project = await _create_project(db_session)
    await _add_history(db_session, project, "intake", "ai", "user")
    llm = _RecordingLLM()

    await ChatService(db_session).judge_points(project, llm=llm)

    prompt = str(llm.judge_messages[0][-1].content)
    assert prompt.endswith(_LATEST_QUESTION_NOTE.format(points="scale(規模)"))


async def test_judge_points_sets_scale_only_when_scale_point_is_settled(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    await _add_history(db_session, project, "user")
    service = ChatService(db_session)

    await service.judge_points(
        project, llm=FakeLLM(structured=HearingPointsJudgement(scale="public"))
    )
    assert project.hearing_scale is None

    await service.judge_points(
        project,
        llm=FakeLLM(
            structured=HearingPointsJudgement(
                scale="internal",
                points=[
                    JudgedPointOut(key="scale", status="decided", summary="社内", evidence=[1])
                ],
            )
        ),
    )
    assert project.hearing_scale == "internal"


async def test_get_hearing_completion_route_returns_stored_check(db_session: AsyncSession) -> None:
    """GET /projects/{id}/hearing-completion は、チャットの送信時に保存した判定をそのまま返す
    (LLMを呼ばないので、get_gemini_llmを差し替えなくてよい。観点の表を持たない以前の
    プロジェクトの5条件の判定も、同じくそのまま返す)。"""
    project = await _create_project(db_session)
    expected = HearingCompletionCheck(
        is_sufficient=True, summary="備品予約システムの要件が整理できました。", missing_points=[]
    )
    project.hearing_check = expected.model_dump()

    result = await get_hearing_completion(db_session, project)

    assert result == expected


async def test_get_hearing_completion_route_is_insufficient_before_any_check(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)

    result = await get_hearing_completion(db_session, project)

    assert result.is_sufficient is False


async def test_stream_reply_presents_summary_without_llm_reply_when_sufficient(
    db_session: AsyncSession,
) -> None:
    """必須の観点がそろったら、LLMで返信を作らず、観点の表から組み立てたまとめを返して判定を
    保存する(発言の回数の下限は無い。1回目の発言でも、必須がそろえば十分)。"""
    project = await _create_project(db_session)
    llm = _RecordingLLM(_all_decided(1, scale="internal"))

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="以上です", llm=llm
        )
    ]

    assert llm.reply_messages == []
    assert len(chunks) == 1
    assert "- 規模: 社内" in chunks[0]
    assert "- 可用性: 可用性の決定" in chunks[0]
    assert "この内容で設計書を生成する" in chunks[0]
    assert project.hearing_check is not None
    assert project.hearing_check["is_sufficient"] is True
    assert project.hearing_check["missing_points"] == []
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert (history[-1].sender, history[-1].message) == ("ai", chunks[0])


async def test_stream_reply_passes_open_required_points_to_reply_when_insufficient(
    db_session: AsyncSession,
) -> None:
    """足りなければ通常の返信を作り、未決の必須の観点を(規模から順に)システムプロンプトに足す。"""
    project = await _create_project(db_session)
    llm = _RecordingLLM()

    async for _ in ChatService(db_session).stream_reply(
        project, user_message="はじめまして", llm=llm
    ):
        pass

    system_prompt = llm.reply_messages[0][0].content
    assert "[まだ確認できていない観点]\n- 規模(例: " in system_prompt
    # 未決の必須が残っている間は、返信のAIに完了を宣言させない
    assert _MISSING_POINTS_NOTE in system_prompt
    assert project.hearing_check is not None
    assert project.hearing_check["is_sufficient"] is False
    assert project.hearing_check["missing_points"][0] == "規模"


async def test_stream_reply_drops_completion_claims_while_required_points_are_open(
    db_session: AsyncSession,
) -> None:
    """未決の必須が残っている間に返信のAIが「そろった」と書いても、その文はコードが除く。"""
    project = await _create_project(db_session)
    llm = _reply_llm(
        "必要な要件がすべて揃いました。準備が整いました。右上のボタンから設計書を生成してください。"
    )

    chunks = [
        c async for c in ChatService(db_session).stream_reply(project, user_message="はい", llm=llm)
    ]

    text = "".join(c for c in chunks if isinstance(c, str))
    assert "揃いました" not in text
    assert "準備が整い" not in text
    assert "生成してください" not in text


def test_drop_completion_claims_keeps_other_sentences() -> None:
    question = "夜中に止まったら、どのくらいで困りますか？"
    message = f"承知しました。これで要件がそろいました。\n{question}"

    assert _drop_completion_claims(message) == f"承知しました。\n{question}"


def test_build_messages_shows_done_when_of_card_targets() -> None:
    """カードにする観点には、決まったと言える条件も渡す(一語で決まる答えを引き出さないため)。"""
    messages = _build_messages([], _project(), card_targets=[point_def("security")])

    assert f"決まったと言える条件: {point_def('security').done_when}" in messages[0].content


async def test_stream_reply_keeps_table_and_clears_check_when_judgement_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """判定に失敗しても返信は続ける。観点の表は前のまま残し、古い判定で生成ボタンが残らないよう
    保存済みの完了判定は消す。返信には、残した表の未決の必須を渡す。"""
    project = await _create_project(db_session)
    states = initial_states()
    states["scale"] = PointState("scale", "decided", "個人", ("x",))
    await HearingPointRepository(db_session).save_states(project.id, states.values())
    project.hearing_check = HearingCompletionCheck(is_sufficient=True, summary="古い").model_dump()

    async def _failing_judge(self, project, *, llm=None):  # noqa: ANN001
        raise GenerationFailedError("失敗")

    monkeypatch.setattr(ChatService, "judge_points", _failing_judge)
    llm = _RecordingLLM()

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="はじめまして", llm=llm
        )
    ]

    # 返信に問いもカードも無いので、次に聞く未決の必須をコードが足す
    assert chunks == [
        "こんにちは\n\n次に、目的・課題について教えてください。"
        "(例: これが無いと、今は何に困っていますか)"
    ]
    assert project.hearing_check is None
    assert "- 目的・課題(例: " in llm.reply_messages[0][0].content
    view = await ChatService(db_session).points_view(project)
    assert view.stale is True
    assert view.is_sufficient is False
    assert view.points[0].status == "decided"


async def test_points_view_of_new_project_lists_all_points_as_open(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)

    view = await get_hearing_points(db_session, project)

    assert (view.legacy, view.stale, view.is_sufficient, view.scale) == (False, False, False, None)
    assert [p.key for p in view.points] == [d.key for d in POINT_DEFS]
    assert all(p.status == "open" for p in view.points)
    # 規模が未決の間は「個人・小規模」の列で必須を示す
    assert {p.key for p in view.points if p.required} >= {"scale", "tech", "security"}
    assert not next(p for p in view.points if p.key == "daily_flow").required


async def test_points_view_marks_project_with_only_old_check_as_legacy(
    db_session: AsyncSession,
) -> None:
    project = await _create_project(db_session)
    project.hearing_check = HearingCompletionCheck(is_sufficient=True, summary="5条件").model_dump()

    view = await ChatService(db_session).points_view(project)

    assert view.legacy is True
    assert view.is_sufficient is False


async def test_points_view_after_judgement_labels_evidence(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    await _add_history(db_session, project, "intake", "ai")
    llm = _RecordingLLM(_all_decided(2))

    async for _ in ChatService(db_session).stream_reply(project, user_message="以上", llm=llm):
        pass
    view = await ChatService(db_session).points_view(project)

    assert (view.legacy, view.stale, view.is_sufficient, view.scale) == (
        False,
        False,
        True,
        "personal",
    )
    assert [e.label for e in view.points[0].evidence] == ["発言1"]


def test_hearing_prompt_leaves_summary_and_documents_to_the_system() -> None:
    """ヒアリングのAIは、確定の確認・まとめ・設計書の本文をチャットに書かない(判定とまとめは
    システムが行い、生成は画面のボタンで行う)。"""
    assert "まとめ(サマリ)" in _HEARING_SYSTEM_PROMPT
    assert "設計書(要件定義書など)の本文" in _HEARING_SYSTEM_PROMPT
    assert "画面のボタン" in _HEARING_SYSTEM_PROMPT


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
        lambda: _opening_llm("【確認したい事】\n・想定ユーザー"),
    )

    project_read = await create_project(
        session=db_session,
        current_user=user,
        name="トレンド調査",
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
        name="トレンド調査",
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


def _opening_llm(text: str, judgement: HearingPointsJudgement | None = None) -> FakeLLM:
    """最初の発話用の偽のLLM。作成時の判定は judgement(既定は何も決まらない)、発話は text。"""
    return FakeLLM(
        structured=judgement or _INSUFFICIENT,
        structured_by_schema={HearingReplyOut: HearingReplyOut(message=text)},
    )


async def test_generate_opening_reply_persists_ai_message(db_session: AsyncSession) -> None:
    """作成時に初期入力を判定してから、最初の発話を作る。規模が未決なら規模のカードを付け、案内の
    文をコードが足す。判定の結果は観点の表と完了判定に保存する。"""
    project = await _create_project(db_session)
    await ChatHistoryRepository(db_session).add(
        project_id=project.id, sender="intake", message="概要: 備品予約"
    )
    fake_llm = _opening_llm("以上を元に詳細のヒアリングを進めていきます。")
    service = ChatService(db_session)

    reply = await service.generate_opening_reply(project, llm=fake_llm)

    assert fake_llm.structured_output_calls == [HearingPointsJudgement, HearingReplyOut]
    assert reply == (
        "以上を元に詳細のヒアリングを進めていきます。\n\n" + _OPENING_SCALE_CARD_LINE
    )
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert [(h.sender, h.message) for h in history] == [("intake", "概要: 備品予約"), ("ai", reply)]
    assert history[1].cards is not None
    assert [c["point_key"] for c in history[1].cards] == ["scale"]
    assert project.hearing_check is not None
    assert len(await HearingPointRepository(db_session).list_for_project(project.id)) == len(
        POINT_DEFS
    )


async def test_generate_opening_reply_skips_points_written_in_intake_but_asks_scale(
    db_session: AsyncSession,
) -> None:
    """初期入力に書いてある観点(目的)は作成時の判定で決まる。規模は初期入力から推し量らず
    (利用者の発言が根拠のときだけ取り込む)、最初の問いは規模のカードになる。"""
    project = await _create_project(db_session)
    await ChatHistoryRepository(db_session).add(
        project_id=project.id, sender="intake", message="社内の在庫の見える化"
    )
    judgement = HearingPointsJudgement(
        scale="internal",
        points=[
            JudgedPointOut(key="scale", status="decided", summary="社内", evidence=[1]),
            JudgedPointOut(key="purpose", status="decided", summary="在庫の見える化", evidence=[1]),
            JudgedPointOut(key="tech", status="adopted", summary="PostgreSQL", evidence=[1]),
        ],
    )
    llm = _RecordingLLM(judgement, reply=HearingReplyOut(message="伺います"))

    reply = await ChatService(db_session).generate_opening_reply(project, llm=llm)

    assert project.hearing_scale is None
    states = {
        r.point_key: r.status
        for r in await HearingPointRepository(db_session).list_for_project(project.id)
    }
    assert states["purpose"] == "decided"
    assert states["scale"] == "open"
    assert states["tech"] == "open"  # 初期入力だけを根拠にした採用は取り込まない
    assert reply.endswith(_OPENING_SCALE_CARD_LINE)


async def test_generate_opening_reply_survives_judgement_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """作成時の判定に失敗しても最初の発話は作る。完了判定は無いまま(最初の送信で判定し直す)。"""
    project = await _create_project(db_session)

    async def _failing_judge(self, project, *, llm=None):  # noqa: ANN001
        raise GenerationFailedError("失敗")

    monkeypatch.setattr(ChatService, "judge_points", _failing_judge)

    reply = await ChatService(db_session).generate_opening_reply(
        project, llm=_opening_llm("開始します")
    )

    assert reply.startswith("開始します")
    assert project.hearing_check is None


def test_build_messages_merges_template_system_prompt() -> None:
    template = PromptTemplate(
        name="Webアプリケーション標準", target_type="Web", system_prompt="Web案件向けの追加指示"
    )

    messages = _build_messages([], _project(), template)

    assert isinstance(messages[0], SystemMessage)
    assert "Web案件向けの追加指示" in messages[0].content


def test_build_messages_omits_template_section_when_none() -> None:
    messages = _build_messages([], _project(), None)

    assert isinstance(messages[0], SystemMessage)
    assert "選択されたテンプレート" not in messages[0].content


async def test_generate_opening_reply_merges_template_system_prompt(
    db_session: AsyncSession,
) -> None:
    user = User(email="owner-template@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    template = PromptTemplate(
        name="API向け", target_type="API", system_prompt="API設計の観点を優先する"
    )
    db_session.add(template)
    await db_session.flush()
    project = Project(user_id=user.id, title="p", template_id=template.id)
    db_session.add(project)
    await db_session.flush()
    llm = _RecordingLLM(reply=HearingReplyOut(message="開始します"))
    service = ChatService(db_session)

    await service.generate_opening_reply(project, llm=llm)

    sent_system_message = llm.reply_messages[0][0]
    assert "API設計の観点を優先する" in sent_system_message.content
    assert "最初の問いの聞き方は" in llm.reply_messages[0][-1].content


# ── 選択肢カード ──


def _availability_card(card_id: str = "c-avail", *, selected: int | None = None) -> Card:
    return Card(
        id=card_id,
        point_key="availability",
        question="夜中に止まっていたら、どのくらいで困りますか",
        options=(
            CardOption("半日止まってよい", "費用は今のまま"),
            CardOption("1時間で戻す", "監視が要る"),
        ),
        recommended=0,
        reason="個人の規模のため",
        selected=selected,
    )


async def _project_with_scale_decided(session: AsyncSession) -> Project:
    """規模が決まったプロジェクト(返信のカードをLLMに作らせる状態)。"""
    project = await _create_project(session)
    states = initial_states()
    states["scale"] = PointState("scale", "decided", "個人", ("x",))
    await HearingPointRepository(session).save_states(project.id, states.values())
    project.hearing_scale = "personal"
    return project


async def _add_ai_with_cards(session: AsyncSession, project: Project, *cards: Card) -> ChatHistory:
    return await ChatHistoryRepository(session).add(
        project_id=project.id,
        sender="ai",
        message="質問です",
        cards=[card_to_dict(card) for card in cards],
    )


async def test_stream_reply_records_card_answers_as_adopted_before_judging(
    db_session: AsyncSession,
) -> None:
    """カードの選択は、LLMを呼ばずにコードが「AIの提案を採用」と記録する。選んだ内容と文を1つの
    発言にし、その発言を根拠にする。判定のLLMには、採用が入った表を見せる。"""
    project = await _project_with_scale_decided(db_session)
    ai = await _add_ai_with_cards(db_session, project, _availability_card())
    llm = _RecordingLLM()

    async for _ in ChatService(db_session).stream_reply(
        project,
        user_message="補足です",
        card_answers=[CardAnswer(card_id="c-avail", option=1)],
        llm=llm,
    ):
        pass

    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    user = history[1]
    assert (user.sender, user.message) == ("user", "【可用性】1時間で戻す\n\n補足です")
    assert ai.cards is not None and ai.cards[0]["selected"] == 1
    rows = {
        r.point_key: r
        for r in await HearingPointRepository(db_session).list_for_project(project.id)
    }
    assert (rows["availability"].status, rows["availability"].summary) == ("adopted", "1時間で戻す")
    assert rows["availability"].evidence == [str(user.id)]
    assert "availability|可用性|" in str(llm.judge_messages[0][-1].content)
    assert "|adopted|1時間で戻す" in str(llm.judge_messages[0][-1].content)


async def test_stream_reply_sets_scale_from_scale_card_answer(db_session: AsyncSession) -> None:
    project = await _create_project(db_session)
    await _add_ai_with_cards(db_session, project, scale_card("c-scale"))

    async for _ in ChatService(db_session).stream_reply(
        project,
        user_message="",
        card_answers=[CardAnswer(card_id="c-scale", option=1)],
        llm=_RecordingLLM(),
    ):
        pass

    assert project.hearing_scale == "internal"
    view = await ChatService(db_session).points_view(project)
    assert view.points[0].status == "adopted"


@pytest.mark.parametrize(
    "answer",
    [
        CardAnswer(card_id="unknown", option=0),  # 最新のAIの発言のカードではない
        CardAnswer(card_id="c-done", option=0),  # 回答済み
        CardAnswer(card_id="c-avail", option=5),  # 範囲の外
    ],
)
async def test_stream_reply_rejects_invalid_card_answers_without_saving(
    db_session: AsyncSession, answer: CardAnswer
) -> None:
    project = await _project_with_scale_decided(db_session)
    await _add_ai_with_cards(
        db_session, project, _availability_card(), _availability_card("c-done", selected=0)
    )

    with pytest.raises(InvalidCardAnswerError):
        async for _ in ChatService(db_session).stream_reply(
            project, user_message="", card_answers=[answer], llm=_RecordingLLM()
        ):
            pass

    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert [h.sender for h in history] == ["ai"]


async def test_stream_reply_rejects_answers_to_cards_of_older_ai_message(
    db_session: AsyncSession,
) -> None:
    """選べるのは最新のAIの発言のカードだけ(古い画面から送られた回答は拒む)。"""
    project = await _project_with_scale_decided(db_session)
    await _add_ai_with_cards(db_session, project, _availability_card())
    await ChatHistoryRepository(db_session).add(project_id=project.id, sender="ai", message="次")

    with pytest.raises(InvalidCardAnswerError):
        async for _ in ChatService(db_session).stream_reply(
            project,
            user_message="",
            card_answers=[CardAnswer(card_id="c-avail", option=0)],
            llm=_RecordingLLM(),
        ):
            pass


async def test_stream_reply_keeps_only_cards_for_targets_and_yields_them_after_text(
    db_session: AsyncSession,
) -> None:
    """1回の返信で聞く問いは1つ。LLMのカードは、聞く観点(未決の必須の先頭)のものを1枚だけ残して
    保存し、本文の後にReplyCardsで返す。返信のプロンプトには、問いを1つにする指示と聞く観点を足す。"""
    project = await _project_with_scale_decided(db_session)

    def _out(key: str) -> CardOut:
        return CardOut(
            point_key=key,  # type: ignore[arg-type]
            question=f"{key}の問い",
            options=[CardOptionOut(label="A", result="a"), CardOptionOut(label="B", result="b")],
            recommended=1,
            reason="理由",
        )

    llm = _RecordingLLM(
        reply=HearingReplyOut(
            message="教えてください",
            cards=[_out("users"), _out("purpose"), _out("purpose")],
        )
    )

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="はじめまして", llm=llm
        )
    ]

    assert chunks[0] == "教えてください"
    assert isinstance(chunks[1], ReplyCards)
    # users は聞く観点の外、purpose の2枚目は捨てる
    assert [c["point_key"] for c in chunks[1].cards] == ["purpose"]
    system_prompt = llm.reply_messages[0][0].content
    assert "問いを1つだけ聞いてください" in system_prompt
    assert "- purpose: 目的・課題" in system_prompt
    assert "- users:" not in system_prompt
    assert "[まだ確認できていない観点]\n- 目的・課題(例: " in system_prompt
    assert "- 利用者と使う場面(例: " not in system_prompt
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert history[-1].cards == chunks[1].cards


async def test_stream_reply_asks_scale_with_card_made_by_code(db_session: AsyncSession) -> None:
    """規模が未決なら、規模のカードだけをコードが出す(LLMのカードは使わない)。"""
    project = await _create_project(db_session)
    llm = _RecordingLLM(
        reply=HearingReplyOut(
            message="",
            cards=[
                CardOut(
                    point_key="scale",
                    question="LLMの問い",
                    options=[
                        CardOptionOut(label="A", result=""),
                        CardOptionOut(label="B", result=""),
                    ],
                    recommended=0,
                    reason="",
                )
            ],
        )
    )

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="はじめまして", llm=llm
        )
    ]

    # 本文が空でもカードがあれば、決まった文で返す
    assert chunks[0] == _EMPTY_REPLY_WITH_CARDS
    assert isinstance(chunks[1], ReplyCards)
    assert [c["question"] for c in chunks[1].cards] == [scale_card("x").question]
    assert _CODE_CARD_NOTE.format(label="規模") in llm.reply_messages[0][0].content


async def test_stream_reply_fails_when_reply_is_empty_without_cards(
    db_session: AsyncSession,
) -> None:
    project = await _project_with_scale_decided(db_session)
    llm = _RecordingLLM(reply=HearingReplyOut(message=" "))

    with pytest.raises(GenerationFailedError):
        async for _ in ChatService(db_session).stream_reply(
            project, user_message="はじめまして", llm=llm
        ):
            pass


def test_build_messages_appends_cards_to_ai_message() -> None:
    entry = ChatHistory(
        sender="ai", message="質問です", cards=[card_to_dict(_availability_card())]
    )

    messages = _build_messages([entry], _project())

    assert isinstance(messages[1], AIMessage)
    assert str(messages[1].content).startswith("質問です\n\n[カード: 可用性] 夜中に")
    assert "1. 半日止まってよい(おすすめ) ── 費用は今のまま" in str(messages[1].content)


def test_hearing_message_request_requires_message_or_card_answers() -> None:
    assert HearingMessageRequest(card_answers=[CardAnswer(card_id="c", option=0)]).message == ""
    with pytest.raises(ValidationError):
        HearingMessageRequest(message="  ")



# ── 観点の表から対象外にする ──


async def _project_missing_only(session: AsyncSession, *keys: str) -> Project:
    """keys 以外の必須がすべて決まったプロジェクト(個人・小規模)。"""
    project = await _create_project(session)
    states = initial_states()
    for d in POINT_DEFS:
        if d.key not in keys:
            states[d.key] = PointState(d.key, "decided", f"{d.label}の決定", ("x",))
    await HearingPointRepository(session).save_states(project.id, states.values())
    project.hearing_scale = "personal"
    return project


async def test_mark_point_not_applicable_rebuilds_completion_without_llm(
    db_session: AsyncSession,
) -> None:
    """未決の必須を画面から対象外にすると、LLMを呼ばずに完了判定を作り直す。残りが無ければ十分に
    なり、生成のボタンが出る(判定のAIが決められない観点で行き詰まらないための逃げ道)。"""
    project = await _project_missing_only(db_session, "purpose", "performance")
    service = ChatService(db_session)

    view = await update_hearing_point(
        "purpose", HearingPointUpdate(status="not_applicable"), db_session, project
    )
    assert view.is_sufficient is False
    assert project.hearing_check is not None
    assert project.hearing_check["missing_points"] == ["性能・拡張性"]
    purpose = next(p for p in view.points if p.key == "purpose")
    assert (purpose.status, purpose.summary, purpose.evidence) == (
        "not_applicable",
        "後で決める(画面で対象外にした)",
        [],
    )

    view = await service.mark_point_not_applicable(project, "performance")
    assert view.is_sufficient is True
    assert project.hearing_check["is_sufficient"] is True
    assert "目的・課題: 対象外(後で決める(画面で対象外にした))" in project.hearing_check["summary"]


async def test_mark_point_not_applicable_rejects_unknown_decided_or_generating(
    db_session: AsyncSession,
) -> None:
    project = await _project_missing_only(db_session, "purpose")
    service = ChatService(db_session)

    with pytest.raises(HearingPointNotFoundError):
        await service.mark_point_not_applicable(project, "unknown")
    with pytest.raises(HearingPointNotOpenError):
        await service.mark_point_not_applicable(project, "tech")
    project.status = "generating"
    with pytest.raises(DocGenerationInProgressError):
        await service.mark_point_not_applicable(project, "purpose")


async def test_mark_point_not_applicable_moves_completed_project_to_revising(
    db_session: AsyncSession,
) -> None:
    project = await _project_missing_only(db_session, "purpose")
    project.status = "completed"

    await ChatService(db_session).mark_point_not_applicable(project, "purpose")

    assert project.status == "revising"


# ── 取りこぼした設問の付け直し・問いの保証・任意の観点 ──


async def _project_required_decided(session: AsyncSession) -> Project:
    """必須(個人・小規模)だけが決まり、任意の観点は未決のプロジェクト。"""
    project = await _create_project(session)
    states = initial_states()
    for d in POINT_DEFS:
        if is_required(d.key, "personal"):
            states[d.key] = PointState(d.key, "decided", f"{d.label}の決定", ("x",))
    await HearingPointRepository(session).save_states(project.id, states.values())
    project.hearing_scale = "personal"
    return project


def _security_card(card_id: str = "c-sec") -> Card:
    return Card(
        id=card_id,
        point_key="security",
        question="誰に見られたら困りますか",
        options=(CardOption("本人だけ", "今の範囲"), CardOption("チームで共有", "権限が要る")),
        recommended=0,
        reason="",
    )


async def test_stream_reply_carries_over_unanswered_cards_to_next_reply(
    db_session: AsyncSession,
) -> None:
    """2枚のカードの片方だけを送ると、残りのカードを次の返信にコードが付け直す(AIに頼らない)。
    付け直す観点は、LLMに渡すカードの候補から外す。"""
    project = await _project_with_scale_decided(db_session)
    await _add_ai_with_cards(db_session, project, _availability_card(), _security_card())
    llm = _RecordingLLM()

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project,
            user_message="",
            card_answers=[CardAnswer(card_id="c-avail", option=0)],
            llm=llm,
        )
    ]

    assert isinstance(chunks[1], ReplyCards)
    carried = chunks[1].cards
    assert [c["point_key"] for c in carried] == ["security"]
    assert carried[0]["id"] != "c-sec"
    assert carried[0]["selected"] is None
    assert "- security:" not in llm.reply_messages[0][0].content


async def test_stream_reply_does_not_carry_over_cards_of_decided_points(
    db_session: AsyncSession,
) -> None:
    """答えなかったカードでも、観点が文で決まっていれば付け直さない。"""
    project = await _project_with_scale_decided(db_session)
    await _add_ai_with_cards(db_session, project, _security_card())
    judgement = HearingPointsJudgement(
        points=[JudgedPointOut(key="security", status="decided", summary="本人だけ", evidence=[1])]
    )
    await ChatHistoryRepository(db_session).add(project_id=project.id, sender="intake", message="x")

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="本人だけが見られればよい", llm=_RecordingLLM(judgement)
        )
    ]

    assert all(not isinstance(chunk, ReplyCards) for chunk in chunks)


async def test_stream_reply_adds_next_question_when_reply_has_no_question(
    db_session: AsyncSession,
) -> None:
    """返信のAIが問いもカードも出さないとき、次に聞く未決の必須をコードが本文に足す
    (止まったように見えないように)。本文に問い(「？」、または「〜ください。」などの言い切り)が
    あれば足さない。"""
    project = await _project_with_scale_decided(db_session)
    llm = _RecordingLLM(reply=HearingReplyOut(message="設計を進めます。"))

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="以上です", llm=llm
        )
    ]
    assert chunks == [
        "設計を進めます。\n\n"
        + _NEXT_QUESTION_TEMPLATE.format(
            label="目的・課題", question="これが無いと、今は何に困っていますか"
        )
    ]

    asked = _RecordingLLM(reply=HearingReplyOut(message="目的を教えてください？"))
    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="続き", llm=asked
        )
    ]
    assert chunks == ["目的を教えてください？"]

    # 「？」が無くても、依頼・問いの言い切りで終わる本文は問いとみなし、二重に足さない
    asking = "利用者と使う場面について教えてください。"
    requested = _RecordingLLM(reply=HearingReplyOut(message=asking))
    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="続き2", llm=requested
        )
    ]
    assert chunks == [asking]


async def test_stream_reply_continues_with_optional_points_when_asked(
    db_session: AsyncSession,
) -> None:
    """必須がそろっていても、利用者が任意の観点を続けると選んだら、まとめでなく未決の任意の観点を
    聞く(候補は未決の任意)。完了判定は十分のまま(生成のボタンは出続ける)。"""
    project = await _project_required_decided(db_session)
    llm = _RecordingLLM(reply=HearingReplyOut(message="任意の点を伺います？"))

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="任意も", continue_optional=True, llm=llm
        )
    ]

    assert chunks == ["任意の点を伺います？"]
    system_prompt = llm.reply_messages[0][0].content
    assert "[まだ確認できていない観点]\n- 利用者の1日の流れ(例: " in system_prompt
    assert "- daily_flow: 利用者の1日の流れ" in system_prompt
    assert project.hearing_check is not None and project.hearing_check["is_sufficient"] is True


async def test_stream_reply_continues_optional_after_answering_optional_card(
    db_session: AsyncSession,
) -> None:
    """任意の観点のカードに答えて送ったら、そのまま任意の観点を続ける。"""
    project = await _project_required_decided(db_session)
    card = Card(
        id="c-flow",
        point_key="daily_flow",
        question="1日のどこで使いますか",
        options=(CardOption("朝", ""), CardOption("夜", "")),
        recommended=0,
        reason="",
    )
    await _add_ai_with_cards(db_session, project, card)
    llm = _RecordingLLM(reply=HearingReplyOut(message="次も伺います？"))

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project,
            user_message="",
            card_answers=[CardAnswer(card_id="c-flow", option=1)],
            llm=llm,
        )
    ]

    assert chunks == ["次も伺います？"]
    assert "- daily_flow:" not in llm.reply_messages[0][0].content  # 答えた観点は候補から外れる


async def test_stream_reply_returns_summary_when_sufficient_and_not_continuing(
    db_session: AsyncSession,
) -> None:
    """必須がそろっていて、任意を続けると選んでいなければ(自由な文)、まとめを返す。"""
    project = await _project_required_decided(db_session)
    llm = _RecordingLLM()

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="確認です", llm=llm
        )
    ]

    assert llm.reply_messages == []
    assert "ヒアリングの内容をまとめました" in chunks[0]



# ── カードだけの回は判定を呼ばない(読むべき新しい文が無いため) ──


async def _judged_project(session: AsyncSession) -> Project:
    """規模が決まり、判定が一度済んだ(完了判定がある)プロジェクト。"""
    project = await _project_with_scale_decided(session)
    project.hearing_check = HearingCompletionCheck(is_sufficient=False, summary="").model_dump()
    return project


async def test_card_only_turn_skips_judgement_and_updates_completion_from_table(
    db_session: AsyncSession,
) -> None:
    project = await _judged_project(db_session)
    await _add_ai_with_cards(db_session, project, _availability_card())
    llm = _RecordingLLM()

    async for _ in ChatService(db_session).stream_reply(
        project,
        user_message="",
        card_answers=[CardAnswer(card_id="c-avail", option=0)],
        llm=llm,
    ):
        pass

    assert llm.judge_messages == []  # 返信の1回だけ
    assert len(llm.reply_messages) == 1
    assert project.hearing_check is not None
    assert "可用性" not in project.hearing_check["missing_points"]


async def test_turn_with_text_still_calls_judgement(db_session: AsyncSession) -> None:
    project = await _judged_project(db_session)
    await _add_ai_with_cards(db_session, project, _availability_card())
    llm = _RecordingLLM()

    async for _ in ChatService(db_session).stream_reply(
        project,
        user_message="補足です",
        card_answers=[CardAnswer(card_id="c-avail", option=0)],
        llm=llm,
    ):
        pass

    assert len(llm.judge_messages) == 1


async def test_card_only_turn_calls_judgement_when_not_judged_yet_or_stale(
    db_session: AsyncSession,
) -> None:
    """まだ一度も判定していない・直近の判定に失敗した(完了判定が無い)ときは、カードだけでも
    判定を呼ぶ(初期入力や、判定に失敗した回の文を読み落とさないため)。"""
    project = await _project_with_scale_decided(db_session)  # 完了判定なし
    await _add_ai_with_cards(db_session, project, _availability_card())
    llm = _RecordingLLM()

    async for _ in ChatService(db_session).stream_reply(
        project,
        user_message="",
        card_answers=[CardAnswer(card_id="c-avail", option=0)],
        llm=llm,
    ):
        pass

    assert len(llm.judge_messages) == 1


async def test_continue_optional_only_turn_stores_fixed_message_without_judgement(
    db_session: AsyncSession,
) -> None:
    project = await _project_required_decided(db_session)
    project.hearing_check = HearingCompletionCheck(is_sufficient=True, summary="x").model_dump()
    llm = _RecordingLLM(reply=HearingReplyOut(message="任意の点を伺います？"))

    async for _ in ChatService(db_session).stream_reply(
        project, user_message="", continue_optional=True, llm=llm
    ):
        pass

    assert llm.judge_messages == []
    history = await ChatHistoryRepository(db_session).list_for_project(project.id)
    assert history[0].message == "任意の項目についてもヒアリングを続けてください"
    assert HearingMessageRequest(continue_optional=True).message == ""


async def test_carried_card_is_reasked_without_llm_card(db_session: AsyncSession) -> None:
    """カードに答えず文を送ったら、観点がまだ未決なら同じカードを付け直し、それを今回の問いにする
    (LLMにはカードを作らせず、本文で質問させない)。"""
    project = await _judged_project(db_session)
    await _add_ai_with_cards(db_session, project, _security_card())
    llm = _RecordingLLM(reply=HearingReplyOut(message="承知しました", cards=[]))

    chunks = [
        chunk
        async for chunk in ChatService(db_session).stream_reply(
            project, user_message="ちょっと考えます", llm=llm
        )
    ]

    assert isinstance(chunks[1], ReplyCards)
    assert [c["point_key"] for c in chunks[1].cards] == ["security"]
    system_prompt = llm.reply_messages[0][0].content
    assert _CODE_CARD_NOTE.format(label="セキュリティ") in system_prompt
    assert "問いを1つだけ聞いてください" not in system_prompt
