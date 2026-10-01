import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graph.workflow import get_chat_workflow
from app.core.config import settings
from app.models.conversation import Conversation, Message
from app.repositories.conversation import ConversationRepository
from app.services.errors import ConversationNotFoundError
from app.services.llm_retry import invoke_with_retry
from app.services.rate_limit import RateLimit, RateLimiter


class ChatService:
    """会話へのメッセージ送信、レート制限、LLMワークフロー呼び出しを取りまとめるサービス。"""

    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        # session: DB操作用の非同期セッション
        # redis: レート制限カウンタの保存に使うRedisクライアント
        self._session = session
        self._conversations = ConversationRepository(session)
        self._rate_limiter = RateLimiter(
            redis,
            resource="chat_message",
            limits=[
                RateLimit(window_seconds=3600, max_requests=settings.CHAT_RATE_LIMIT_PER_HOUR),
                RateLimit(window_seconds=86400, max_requests=settings.CHAT_RATE_LIMIT_PER_DAY),
            ],
        )

    async def send_message(
        self,
        *,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        message: str,
        bypass_rate_limit: bool = False,
    ) -> tuple[Conversation, Message]:
        """ユーザーからのメッセージを会話に追加し、LLMワークフローで応答を生成して保存する。"""
        if not bypass_rate_limit:
            # 管理者ユーザーなどは呼び出し元の判断でバイパスできるようにしている
            await self._rate_limiter.enforce(str(user_id))

        conversation = await self._get_or_create_conversation(
            user_id=user_id, conversation_id=conversation_id, first_message=message
        )
        await self._conversations.add_message(
            conversation_id=conversation.id, role="user", content=message
        )

        workflow = get_chat_workflow()
        initial_state = {
            "question": message,
            "messages": [],
            "needs_search": False,
            "search_query": "",
            "search_results": [],
            "evaluation": "",
            "answer": "",
        }
        # 再試行とクォータ超過の判定は、Devexの他のLLM呼び出しと同じ共通の部品に寄せる
        # (クォータ超過はLLM_QUOTA_EXCEEDED、規定回数の失敗はLLM_API_ERRORになる)
        result = await invoke_with_retry(lambda: workflow.ainvoke(initial_state))

        assistant_message = await self._conversations.add_message(
            conversation_id=conversation.id, role="assistant", content=result["answer"]
        )
        await self._session.commit()
        return conversation, assistant_message

    async def _get_or_create_conversation(
        self, *, user_id: uuid.UUID, conversation_id: uuid.UUID | None, first_message: str
    ) -> Conversation:
        """conversation_id未指定なら新規会話を作り、指定済みなら所有者チェックの上で取得する。"""
        if conversation_id is None:
            # 新規会話のタイトルは最初のメッセージ冒頭80文字を流用する
            title = first_message[:80]
            return await self._conversations.create(user_id=user_id, title=title)

        conversation = await self._conversations.get_by_id(conversation_id, user_id=user_id)
        if conversation is None:
            raise ConversationNotFoundError(f"Conversation {conversation_id} not found")
        return conversation
