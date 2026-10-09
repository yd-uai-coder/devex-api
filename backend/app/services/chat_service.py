import json
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import extract_text_content, get_gemini_llm
from app.hearing import (
    POINT_DEFS,
    JudgedPoint,
    PointState,
    Scale,
    build_summary,
    initial_states,
    is_required,
    is_sufficient,
    merge_judgement,
    missing_labels,
    number_messages,
    questions_for_reply,
    scale_after,
    to_state,
)
from app.models.chat_history import ChatHistory
from app.models.hearing_point import HearingPoint
from app.models.project import Project
from app.models.prompt_template import PromptTemplate
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.hearing_point import HearingPointRepository
from app.repositories.prompt_template import PromptTemplateRepository
from app.schemas.generation import HearingCompletionCheck
from app.schemas.hearing import (
    EvidenceRead,
    HearingPointRead,
    HearingPointsJudgement,
    HearingPointsRead,
)
from app.services.errors import GenerationFailedError
from app.services.llm_retry import as_llm_error, invoke_with_retry, prompt_char_count

logger = structlog.get_logger(__name__)

_HEARING_SYSTEM_PROMPT = (
    "あなたはシステム開発の要件定義を支援するAIアシスタントです。"
    "ユーザーが最初に入力した概要・実現したいこと・補足(および添付資料の内容)を踏まえ、"
    "目的や課題、コア機能、想定ユーザー、MVPスコープを対話の中で明確にしてください。"
    "初期入力の段階で箇条書きへの分解や機能の構造化は行われていないため、必要に応じて"
    "あなたが提案し、ユーザーの同意・修正を得てください。"
    "返信の際は、ユーザーが確認を求めていない限り、ユーザーが既に提示した内容をそのまま"
    "要約・反復しないでください。要件を確定するために今何が不足しているかを吟味した上で、"
    "その不足点を埋めるための問いかけに絞って簡潔に返信してください"
    "(シンプルな質問、選択肢の提示など)。"
    "要件の確定の確認、ヒアリング内容のまとめ(サマリ)、設計書(要件定義書など)の本文は"
    "チャットに書かないでください。要件がそろったかどうかの判定とまとめの提示はシステムが行い、"
    "設計書は画面のボタンから生成します。"
)

_POINTS_JUDGE_PROMPT = (
    "これまでの対話を読み、下の観点の表の各観点について、状態・決めたこと・根拠を判定してください。"
    "ユーザー側の発言の先頭にある[#n]が、その発言の番号です。\n"
    "状態:\n"
    "・decided: ユーザーが自分の言葉で決めた\n"
    "・adopted: AIが出した提案・選択肢に、ユーザーが同意した\n"
    "・not_applicable: この観点はこのシステムには要らない、とユーザーが答えた\n"
    "・open: まだ決まっていない(聞いていない、答えていない、「決めていない」と答えた)\n"
    "判定の規則:\n"
    "・根拠(evidence)は、その観点を決めたユーザー側の発言の番号だけを挙げる。AIの発言は根拠にしない。\n"
    "・AIが提案・推測しただけで、ユーザーが答えていない観点はopenにする。\n"
    "・今の状態がopen以外の観点は、ユーザーが言い直した場合だけ変える。\n"
    "・summaryは、決めたことを1文で簡潔に書く(openなら空でよい)。\n"
    "・scaleは、規模の観点が決まったときだけ、personal(自分と数人)/internal(社内)/"
    "public(誰でも登録できるサービス)のどれかにする。\n"
    "観点の表(キー|観点|場面で聞く問いの例|今の状態|決めたこと):\n"
    "{table}"
)

# 完了判定が十分になったときの返信。LLMで書かせず、観点の表から組み立てたまとめを見せることで、
# チャットのまとめと画面の生成ボタンが同じ判定から同時に出るようにする
_SUMMARY_REPLY_TEMPLATE = (
    "ヒアリングの内容をまとめました。\n\n{summary}\n\n"
    "この内容でよければ「この内容で設計書を生成する」を押してください。"
    "直したい点があれば、チャットで伝えてください。"
)

_OPENING_TURN_PROMPT = (
    "これはこのプロジェクトのヒアリング対話における、あなたの最初の返信です。"
    "ユーザーから提示された内容をそのまま繰り返さず、次の形式で日本語で回答してください。\n"
    "1. 1行目:「以上を元に詳細のヒアリングを進めていきます。」という一文だけを書く\n"
    "2. 次の行に見出し「【確認したい事】」を書く\n"
    "3. これから対話の中で確認していきたい点を3つ、「・」で始まる箇条書きで簡潔に列挙する\n"
    "4. 1行空けて、「まず、」に続けて箇条書きの1つ目について具体的な質問を1つだけ書く\n"
    "この形式以外の前置きや締めの言葉は書かないでください。"
)


class ChatService:
    """プロジェクトのヒアリングチャット(対話生成・完了判定)を担当するサービス。

    既存の汎用デモ(app/ai/graph/)とは異なりLangGraphを使わない: ヒアリングフローは
    「毎回LLM応答生成→完了判定」という一本道の処理であり、真の分岐を持つLangGraphの
    StateGraphを導入するのは過剰なため、素のLangChainメッセージで組み立てる。
    """

    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._chat_histories = ChatHistoryRepository(session)
        self._prompt_templates = PromptTemplateRepository(session)
        self._hearing_points = HearingPointRepository(session)


    async def list_history(self, project_id: uuid.UUID) -> list[ChatHistory]:
        """プロジェクトのチャット履歴を送信日時の昇順(発生順)で取得する。"""
        return await self._chat_histories.list_for_project(project_id)

    async def stream_reply(
        self, project: Project, *, user_message: str, llm=None
    ) -> AsyncIterator[str]:
        """ユーザーメッセージを永続化し、先に観点の表の判定を行ってから、その結果に応じたAI応答を
        ストリーミングで返す。応答本文の断片を順次yieldし、完了後にAI応答全体をchat_historiesへ、
        観点の状態をhearing_pointsへ、完了判定の結果をprojects.hearing_checkへ保存する。

        - 完了は、必須の観点がすべて未決以外かどうかをコードが決める(app/hearing/points.py)。
        - 十分なら、LLMで返信を作らず、観点の表から組み立てたまとめを決まった形で返す(まとめと
          生成ボタンを同じ判定から同時に出すため)。
        - 足りなければ、未決の必須の観点を渡して通常の返信を作る(AIがそこを聞くように)。
        - 判定に失敗したら、観点の表は前のまま残し、保存済みの完了判定は消す(古い判定で生成ボタンが
          残らないように。表があって完了判定が無い状態を、画面は「表が直近の発言を反映していない」と
          読む)。チャット自体は止めない。

        completed(生成済み)のプロジェクトへ新規メッセージが送られた場合はrevising(修正中)へ
        遷移させる(ユーザーがヒアリング内容を修正し、再生成する意思を示したものとみなす)。
        """
        if project.status == "completed":
            project.status = "revising"
        await self._chat_histories.add(project_id=project.id, sender="user", message=user_message)

        # 「or」の理由：テスト時に本物のGemini APIを叩かせないための差し替え(DI)
        llm = llm or get_gemini_llm()
        try:
            states = await self.judge_points(project, llm=llm)
        except GenerationFailedError:
            logger.warning("hearing_check_failed")
            states = None
        if states is None:
            project.hearing_check = None
            questions = questions_for_reply(
                await self._load_states(project), _as_scale(project.hearing_scale)
            )
        else:
            check = _completion_from(states, project.hearing_scale)
            project.hearing_check = check.model_dump()
            if check.is_sufficient:
                reply = _SUMMARY_REPLY_TEMPLATE.format(summary=check.summary.strip())
                yield reply
                await self._chat_histories.add(project_id=project.id, sender="ai", message=reply)
                await self._session.commit()
                return
            questions = questions_for_reply(states, _as_scale(project.hearing_scale))

        history = await self._chat_histories.list_for_project(project.id)
        template = await self._load_template(project)
        messages = _build_messages(history, project, template, missing_points=questions)

        started = time.monotonic()
        chunks: list[str] = []
        try:
            async for chunk in llm.astream(messages):
                piece = extract_text_content(chunk.content)
                if not piece:
                    continue
                chunks.append(piece)
                yield piece
        except Exception as exc:
            # 再試行はしない(送信済みの断片があるため。llm_retry.pyのdocstring参照)。
            # 失敗の種類だけを共通の例外に揃え、ルートがSSEの`event: error`で伝える
            raise as_llm_error(exc) from exc

        logger.debug(
            "llm_call_succeeded",
            prompt_chars=prompt_char_count(messages),
            latency_ms=round((time.monotonic() - started) * 1000, 1),
        )

        full_reply = "".join(chunks)
        await self._chat_histories.add(project_id=project.id, sender="ai", message=full_reply)
        await self._session.commit()

    async def judge_points(self, project: Project, *, llm=None) -> dict[str, PointState]:
        """これまでの対話から、観点ごとの状態と根拠をLLMに判定させ、観点の表に取り込んで保存する
        (hearing_points・projects.hearing_scale。commitは呼び出し側)。取り込んだ後の表を返す。

        LLMには利用者側の発言に番号を付けて見せ、返った番号をchat_historiesのidに直す。根拠の
        無い「決定」は取り込まない(merge_judgement)。完了かどうかはLLMに聞かない。

        単発呼び出し(ストリーミングではない)のため、一時的な失敗はinvoke_with_retryでリトライする
        """
        history = await self._chat_histories.list_for_project(project.id)
        template = await self._load_template(project)
        previous = await self._load_states(project)
        numbered = number_messages([(str(entry.id), entry.sender) for entry in history])
        numbers = {message_id: number for message_id, number, _ in numbered}
        messages = [
            *_build_messages(history, project, template, numbers=numbers),
            HumanMessage(content=_POINTS_JUDGE_PROMPT.format(table=_points_table(previous))),
        ]

        llm = llm or get_gemini_llm()
        structured_llm = llm.with_structured_output(HearingPointsJudgement)

        async def _call() -> HearingPointsJudgement:
            result = await structured_llm.ainvoke(messages)
            assert isinstance(result, HearingPointsJudgement)
            return result

        result = await invoke_with_retry(_call, messages=messages)
        ids_by_number = {number: message_id for message_id, number in numbers.items()}
        judged = [
            JudgedPoint(
                key=point.key,
                status=point.status,
                summary=point.summary,
                evidence=tuple(
                    dict.fromkeys(ids_by_number[n] for n in point.evidence if n in ids_by_number)
                ),
            )
            for point in result.points
        ]
        states = merge_judgement(previous, judged)
        project.hearing_scale = scale_after(
            states, _as_scale(project.hearing_scale), result.scale
        )
        await self._hearing_points.save_states(project.id, states.values())
        return states

    async def points_view(self, project: Project) -> HearingPointsRead:
        """チャット画面の観点の表(LLMは呼ばない)。表がまだ無いプロジェクトは、全観点を未決で返す。"""
        rows = await self._hearing_points.list_for_project(project.id)
        states = _states_from_rows(rows)
        scale = _as_scale(project.hearing_scale)
        history = await self._chat_histories.list_for_project(project.id)
        labels = {
            message_id: label
            for message_id, _, label in number_messages(
                [(str(entry.id), entry.sender) for entry in history]
            )
        }
        stale = bool(rows) and project.hearing_check is None
        return HearingPointsRead(
            scale=scale,
            legacy=not rows and project.hearing_check is not None,
            stale=stale,
            is_sufficient=bool(rows) and not stale and is_sufficient(states, scale),
            points=[
                HearingPointRead(
                    key=d.key,
                    kind=d.kind,
                    label=d.label,
                    question=d.question,
                    required=is_required(d.key, scale),
                    status=states[d.key].status,
                    summary=states[d.key].summary,
                    evidence=[
                        EvidenceRead(message_id=uuid.UUID(message_id), label=labels[message_id])
                        for message_id in states[d.key].evidence
                        if message_id in labels
                    ],
                )
                for d in POINT_DEFS
            ],
        )

    async def _load_states(self, project: Project) -> dict[str, PointState]:
        return _states_from_rows(await self._hearing_points.list_for_project(project.id))

    def stored_completion(self, project: Project) -> HearingCompletionCheck:
        """直近の発言で行ったヒアリング完了判定の結果を返す(LLMは呼ばない)。まだ判定していない、
        または直近の判定に失敗したプロジェクトは「十分でない」とする。観点の表を持たない以前の
        プロジェクトは、保存してある5条件の判定をそのまま返す。"""
        if project.hearing_check is None:
            return HearingCompletionCheck(is_sufficient=False, summary="", missing_points=[])
        return HearingCompletionCheck.model_validate(project.hearing_check)

    async def generate_opening_reply(self, project: Project, *, llm=None) -> str:
        """ヒアリング開始直後、ユーザー発話を待たずにAIの最初の発話を生成し永続化する
        (docs/external_design.md SCR-004 2.3節: 「AIの最初の発話は初期ヒアリング入力の
        内容を踏まえた理解の要約と確認質問から始まる」)。プロジェクト作成直後に一度だけ
        呼ばれる想定。失敗時は例外を伝播させる(致命的でない扱いは呼び出し側=ルート層で行う)。

        単発呼び出し(ストリーミングではない)のため、check_completionと同様
        invoke_with_retryでリトライする。"""
        history = await self._chat_histories.list_for_project(project.id)
        template = await self._load_template(project)
        messages = [
            *_build_messages(history, project, template),
            HumanMessage(content=_OPENING_TURN_PROMPT),
        ]
        llm = llm or get_gemini_llm()

        async def _call() -> str:
            result = await llm.ainvoke(messages)
            return extract_text_content(result.content)

        reply = await invoke_with_retry(_call, messages=messages)
        await self._chat_histories.add(project_id=project.id, sender="ai", message=reply)
        await self._session.commit()
        return reply

    async def _load_template(self, project: Project) -> PromptTemplate | None:
        """project.template_idが設定されている場合、対応するPromptTemplateを取得する
        (未設定、または存在しないIDの場合はNoneを返し、通常のヒアリングプロンプトのみを使う)。"""
        if project.template_id is None:
            return None
        return await self._prompt_templates.get_by_id(project.template_id)


def _build_messages(
        history: list[ChatHistory],
        project: Project,
        template: PromptTemplate | None = None,
        *,
        missing_points: Sequence[str] = (),
        numbers: Mapping[str, int] | None = None,
) -> list[BaseMessage]:
    """chat_historiesの行(sender+message)を、LangChainのメッセージ列に変換する。

    sender='user'/'intake'/'attachment'(初期ヒアリング入力・添付ファイル抽出結果)はHumanMessage、
    sender='ai'はAIMessageとして扱う。sender='others'(生成後の自己診断結果)はヒアリング中の
    対話には含めない(生成完了後にのみ現れるため、通常この時点では存在しない)。

    environment(開発環境の希望)は`chat_histories`には保存されない(チャット画面には表示
    しない、docs/external_design.md参照)ため、`project.intake`から直接読み取ってHumanMessage
    として注入する。ヒアリング完了判定の5条件目「技術的な制約・希望の確認」の材料として、
    表示の有無に関わらずLLMには常に渡す。

    missing_points(観点の表で未決の必須の観点)は、返信の生成のときだけシステムプロンプトに
    足す。AIが次に何を聞くべきかを、完了判定と同じ基準にそろえるため。

    numbers(発言のid→番号)は、観点の判定のときだけ渡す。利用者側の発言の先頭に`[#n]`を付け、
    LLMが根拠を番号で返せるようにする。開発環境の希望は初期入力の番号で見せる(初期入力の一部のため)。
    """
    system_prompt = _HEARING_SYSTEM_PROMPT
    if template is not None:
        system_prompt += f"\n\n[選択されたテンプレート: {template.name}]\n{template.system_prompt}"
    if missing_points:
        lines = "\n".join(f"- {point}" for point in missing_points)
        system_prompt += f"\n\n[まだ確認できていない観点]\n{lines}"
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    numbers = numbers or {}

    def _prefix(entry_id: str | None) -> str:
        number = numbers.get(entry_id) if entry_id is not None else None
        return f"[#{number}] " if number is not None else ""

    environment = (project.intake or {}).get("environment")
    if environment:
        intake_id = next((str(e.id) for e in history if e.sender == "intake"), None)
        messages.append(
            HumanMessage(
                content=f"{_prefix(intake_id)}[開発環境の希望]\n"
                f"{json.dumps(environment, ensure_ascii=False)}"
            )
        )
    for entry in history:
        if entry.sender == "ai":
            messages.append(AIMessage(content=entry.message))
        elif entry.sender in ("user", "intake", "attachment"):
            messages.append(HumanMessage(content=_prefix(str(entry.id)) + entry.message))
    return messages


def _as_scale(value: str | None) -> Scale | None:
    return value if value in ("personal", "internal", "public") else None  # type: ignore[return-value]


def _states_from_rows(rows: Sequence[HearingPoint]) -> dict[str, PointState]:
    """hearing_pointsの行を観点の表に直す(行の無い観点・知らないキーの行は除いて、未決で埋める)。"""
    states = initial_states()
    for row in rows:
        state = to_state(row.point_key, row.status, row.summary, row.evidence or ())
        if state is not None:
            states[state.key] = state
    return states


def _points_table(states: Mapping[str, PointState]) -> str:
    """判定のプロンプトに埋める観点の表(1行1観点)。"""
    return "\n".join(
        f"{d.key}|{d.label}|{d.question}|{states[d.key].status}|{states[d.key].summary}"
        for d in POINT_DEFS
    )


def _completion_from(states: Mapping[str, PointState], scale: str | None) -> HearingCompletionCheck:
    """観点の表から完了判定を作る(LLMは呼ばない)。十分ならまとめを付ける。"""
    sufficient = is_sufficient(states, _as_scale(scale))
    return HearingCompletionCheck(
        is_sufficient=sufficient,
        summary=build_summary(states, _as_scale(scale)) if sufficient else "",
        missing_points=missing_labels(states, _as_scale(scale)),
    )
