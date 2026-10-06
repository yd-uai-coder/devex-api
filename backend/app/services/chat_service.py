import json
import time
import uuid
from collections.abc import AsyncIterator, Sequence

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import extract_text_content, get_gemini_llm
from app.models.chat_history import ChatHistory
from app.models.project import Project
from app.models.prompt_template import PromptTemplate
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.prompt_template import PromptTemplateRepository
from app.schemas.generation import HearingCompletionCheck
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

_COMPLETION_CHECK_PROMPT = (
    "これまでの対話を読み、要件ヒアリングとして次の5条件を満たしているか厳格に判定してください。\n"
    "(1) 目的・課題が明確である\n"
    "(2) コア機能が最低1つ以上「誰が・何を・なぜ」のレベルで具体化されている\n"
    "(3) 想定ユーザー像が把握できている\n"
    "(4) MVPスコープ(今回作る/作らない)の認識合わせができている\n"
    "(5) 技術的な強い制約・希望の有無を確認できている(環境設定が未入力の場合のみ必須)\n"
    "判定ルール:\n"
    "・各条件は、対話中の【ユーザーの発言】に明確な根拠がある場合にのみ「満たした」とみなす。"
    "AIが提案・推測しただけの内容や、ユーザーが未回答の内容は根拠として認めない。\n"
    "・AIの直近の発言がユーザーへの質問を含み、ユーザーがまだ回答していない場合は、"
    "その質問に関わる条件は未達とする。\n"
    "・「【確認したい事】」としてAIが挙げた項目のうち、ユーザーの回答が得られていないものが"
    "1つでも残っている場合は未達とする。\n"
    "・1つでも判断に迷う条件があれば、is_sufficientはfalseとする(早すぎる完了判定は、"
    "ユーザーに不十分な内容で設計書を生成させてしまうため、遅すぎる判定より不利益が大きい)。\n"
    "5条件をすべて満たした場合のみis_sufficient=trueとし、ユーザーへ提示するための構造化された要約"
    "(summary)を書いてください。"
    "不十分な場合は、summaryは簡潔な現状整理とし、missing_pointsに不足している観点を必ず具体的に列挙してください。"
)

_MIN_USER_TURNS_FOR_COMPLETION = 3

# ユーザー発話の回数が足りないときに完了判定へ足す不足点。対話の観点ではないので、
# 返信の生成に渡す「まだ確認できていない観点」には含めない
_TOO_FEW_TURNS_POINT = "対話がまだ十分に進んでいません"

# 完了判定が十分になったときの返信。LLMで書かせず、判定のsummaryをそのまま見せることで、
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


    async def list_history(self, project_id: uuid.UUID) -> list[ChatHistory]:
        """プロジェクトのチャット履歴を送信日時の昇順(発生順)で取得する。"""
        return await self._chat_histories.list_for_project(project_id)

    async def stream_reply(
        self, project: Project, *, user_message: str, llm=None
    ) -> AsyncIterator[str]:
        """ユーザーメッセージを永続化し、先にヒアリング完了判定を行ってから、その結果に応じたAI応答を
        ストリーミングで返す。応答本文の断片を順次yieldし、完了後にAI応答全体をchat_historiesへ、
        判定の結果をprojects.hearing_checkへ保存する。

        - 十分なら、LLMで返信を作らず、判定のsummaryを決まった形で返す(まとめと生成ボタンを
          同じ判定から同時に出すため)。
        - 足りなければ、判定のmissing_pointsを渡して通常の返信を作る(AIがそこを聞くように)。
        - 判定に失敗したら「足りない」として通常の返信を作り、保存済みの判定は消す(古い判定で
          生成ボタンが残らないように)。チャット自体は止めない。

        completed(生成済み)のプロジェクトへ新規メッセージが送られた場合はrevising(修正中)へ
        遷移させる(ユーザーがヒアリング内容を修正し、再生成する意思を示したものとみなす)。
        """
        if project.status == "completed":
            project.status = "revising"
        await self._chat_histories.add(project_id=project.id, sender="user", message=user_message)

        # 「or」の理由：テスト時に本物のGemini APIを叩かせないための差し替え(DI)
        llm = llm or get_gemini_llm()
        try:
            check: HearingCompletionCheck | None = await self.check_completion(project, llm=llm)
        except GenerationFailedError:
            logger.warning("hearing_check_failed")
            check = None
        project.hearing_check = check.model_dump() if check is not None else None

        if check is not None and check.is_sufficient:
            reply = _SUMMARY_REPLY_TEMPLATE.format(summary=check.summary.strip())
            yield reply
            await self._chat_histories.add(project_id=project.id, sender="ai", message=reply)
            await self._session.commit()
            return

        history = await self._chat_histories.list_for_project(project.id)
        template = await self._load_template(project)
        points = check.missing_points if check is not None else []
        missing_points = [point for point in points if point != _TOO_FEW_TURNS_POINT]
        messages = _build_messages(history, project, template, missing_points=missing_points)

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

    async def check_completion(self, project: Project, *, llm=None) -> HearingCompletionCheck:
        """これまでの対話履歴から、ヒアリングが完了条件(5条件)を満たしたかどうかを判定する。
        十分と判定した場合でも、呼び出し側(ルート層)が構造化サマリを提示し、ユーザーの明示的な
        承認を得てから設計書生成(doc_generator_service)へ進める(即座には生成しない)。

        単発呼び出し(ストリーミングではない)のため、一時的な失敗はinvoke_with_retryでリトライする
        """
        history = await self._chat_histories.list_for_project(project.id)
        template = await self._load_template(project)
        messages = [
            *_build_messages(history, project, template),
            HumanMessage(content=_COMPLETION_CHECK_PROMPT),
        ]

        llm = llm or get_gemini_llm()
        structured_llm = llm.with_structured_output(HearingCompletionCheck)

        async def _call() -> HearingCompletionCheck:
            result = await structured_llm.ainvoke(messages)
            assert isinstance(result, HearingCompletionCheck)
            return result

        result = await invoke_with_retry(_call, messages=messages)
        user_turns = sum(1 for entry in history if entry.sender == "user")
        if result.is_sufficient and user_turns < _MIN_USER_TURNS_FOR_COMPLETION:
            # LLMが早期にtrueと判定しても、実発話が少なすぎる間は完了させない
            return HearingCompletionCheck(
                is_sufficient=False,
                summary=result.summary,
                missing_points=[*result.missing_points, _TOO_FEW_TURNS_POINT],
            )
        return result

    def stored_completion(self, project: Project) -> HearingCompletionCheck:
        """直近の発言で行ったヒアリング完了判定の結果を返す(LLMは呼ばない)。まだ判定していない、
        または直近の判定に失敗したプロジェクトは「十分でない」とする。"""
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
) -> list[BaseMessage]:
    """chat_historiesの行(sender+message)を、LangChainのメッセージ列に変換する。

    sender='user'/'intake'/'attachment'(初期ヒアリング入力・添付ファイル抽出結果)はHumanMessage、
    sender='ai'はAIMessageとして扱う。sender='others'(生成後の自己診断結果)はヒアリング中の
    対話には含めない(生成完了後にのみ現れるため、通常この時点では存在しない)。

    environment(開発環境の希望)は`chat_histories`には保存されない(チャット画面には表示
    しない、docs/external_design.md参照)ため、`project.intake`から直接読み取ってHumanMessage
    として注入する。ヒアリング完了判定の5条件目「技術的な制約・希望の確認」の材料として、
    表示の有無に関わらずLLMには常に渡す。

    missing_points(直前の完了判定で足りなかった観点)は、返信の生成のときだけシステムプロンプトに
    足す。AIが次に何を聞くべきかを、完了判定と同じ基準にそろえるため。
    """
    system_prompt = _HEARING_SYSTEM_PROMPT
    if template is not None:
        system_prompt += f"\n\n[選択されたテンプレート: {template.name}]\n{template.system_prompt}"
    if missing_points:
        lines = "\n".join(f"- {point}" for point in missing_points)
        system_prompt += f"\n\n[まだ確認できていない観点]\n{lines}"
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    environment = (project.intake or {}).get("environment")
    if environment:
        messages.append(
            HumanMessage(content=f"[開発環境の希望]\n{json.dumps(environment, ensure_ascii=False)}")
        )
    for entry in history:
        if entry.sender == "ai":
            messages.append(AIMessage(content=entry.message))
        elif entry.sender in ("user", "intake", "attachment"):
            messages.append(HumanMessage(content=entry.message))
    return messages
