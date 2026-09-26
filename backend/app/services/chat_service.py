import json
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm.gemini import extract_text_content, get_gemini_llm
from app.models.chat_history import ChatHistory
from app.models.project import Project
from app.repositories.chat_history import ChatHistoryRepository
from app.schemas.generation import HearingCompletionCheck
from app.services.llm_retry import invoke_with_retry

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
)

_COMPLETION_CHECK_PROMPT = (
    "これまでの対話を読み、要件ヒアリングとして次の5条件を満たしているか判定してください。\n"
    "(1) 目的・課題が明確である\n"
    "(2) コア機能が最低1つ以上「誰が・何を・なぜ」のレベルで具体化されている\n"
    "(3) 想定ユーザー像が把握できている\n"
    "(4) MVPスコープ(今回作る/作らない)の認識合わせができている\n"
    "(5) 技術的な強い制約・希望の有無を確認できている(環境設定が未入力の場合のみ必須)\n"
    "十分だと判断した場合は、ユーザーへ提示するための構造化された要約(summary)を書いてください。"
    "不十分な場合は、summaryは簡潔な現状整理とし、missing_pointsに不足している観点を具体的に列挙してください。"
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

    async def stream_reply(
        self, project: Project, *, user_message: str, llm=None
    ) -> AsyncIterator[str]:
        """ユーザーメッセージを永続化し、これまでの対話履歴を踏まえたAI応答をストリーミングで生成する。
        応答本文の断片を順次yieldし、ストリーム完了後にAI応答全体をchat_historiesへ保存する。

        completed(生成済み)のプロジェクトへ新規メッセージが送られた場合はrevising(修正中)へ
        遷移させる(ユーザーがヒアリング内容を修正し、再生成する意思を示したものとみなす)。
        """
        if project.status == "completed":
            project.status = "revising"
        await self._chat_histories.add(project_id=project.id, sender="user", message=user_message)
        history = await self._chat_histories.list_for_project(project.id)
        messages = _build_messages(history, project)

        # 「or」の理由：テスト時に本物のGemini APIを叩かせないための差し替え(DI)
        llm = llm or get_gemini_llm()
        chunks: list[str] = []
        async for chunk in llm.astream(messages):
            piece = extract_text_content(chunk.content)
            if not piece:
                continue
            chunks.append(piece)
            yield piece

        full_reply = "".join(chunks)
        await self._chat_histories.add(project_id=project.id, sender="ai", message=full_reply)
        await self._session.commit()

    async def check_completion(self, project: Project, *, llm=None) -> HearingCompletionCheck:
        """これまでの対話履歴から、ヒアリングが完了条件(5条件)を満たしたかどうかを判定する。
        十分と判定した場合でも、呼び出し側(ルート層)が構造化サマリを提示し、ユーザーの明示的な
        承認を得てから設計書生成(doc_generator_service)へ進める(即座には生成しない)。

        単発呼び出し(ストリーミングではない)のため、一時的な失敗はinvoke_with_retryでリトライする
        (docs/implementation_plan.md 4.4節リスク1)。"""
        history = await self._chat_histories.list_for_project(project.id)
        messages = [*_build_messages(history, project), HumanMessage(content=_COMPLETION_CHECK_PROMPT)]

        llm = llm or get_gemini_llm()
        structured_llm = llm.with_structured_output(HearingCompletionCheck)

        async def _call() -> HearingCompletionCheck:
            result = await structured_llm.ainvoke(messages)
            assert isinstance(result, HearingCompletionCheck)
            return result

        return await invoke_with_retry(_call)

    async def generate_opening_reply(self, project: Project, *, llm=None) -> str:
        """ヒアリング開始直後、ユーザー発話を待たずにAIの最初の発話を生成し永続化する
        (docs/external_design.md SCR-004 2.3節: 「AIの最初の発話は初期ヒアリング入力の
        内容を踏まえた理解の要約と確認質問から始まる」)。プロジェクト作成直後に一度だけ
        呼ばれる想定。失敗時は例外を伝播させる(致命的でない扱いは呼び出し側=ルート層で行う)。

        単発呼び出し(ストリーミングではない)のため、check_completionと同様
        invoke_with_retryでリトライする。"""
        history = await self._chat_histories.list_for_project(project.id)
        messages = [*_build_messages(history, project), HumanMessage(content=_OPENING_TURN_PROMPT)]

        llm = llm or get_gemini_llm()

        async def _call() -> str:
            result = await llm.ainvoke(messages)
            return extract_text_content(result.content)

        reply = await invoke_with_retry(_call)
        await self._chat_histories.add(project_id=project.id, sender="ai", message=reply)
        await self._session.commit()
        return reply


def _build_messages(history: list[ChatHistory], project: Project) -> list[BaseMessage]:
    """chat_historiesの行(sender+message)を、LangChainのメッセージ列に変換する。

    sender='user'/'intake'/'attachment'(初期ヒアリング入力・添付ファイル抽出結果)はHumanMessage、
    sender='ai'はAIMessageとして扱う。sender='others'(生成後の自己診断結果)はヒアリング中の
    対話には含めない(生成完了後にのみ現れるため、通常この時点では存在しない)。

    environment(開発環境の希望)は`chat_histories`には保存されない(チャット画面には表示
    しない、docs/external_design.md参照)ため、`project.intake`から直接読み取ってHumanMessage
    として注入する。ヒアリング完了判定の5条件目「技術的な制約・希望の確認」の材料として、
    表示の有無に関わらずLLMには常に渡す。
    """
    messages: list[BaseMessage] = [SystemMessage(content=_HEARING_SYSTEM_PROMPT)]
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
