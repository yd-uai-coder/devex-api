from fastapi import APIRouter

from app.api.deps import CurrentUserDep, RedisDep, SessionDep
from app.schemas.chat import ChatRequest, ChatResponse, MessageRead
from app.services.chat import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def send_message(
    payload: ChatRequest, session: SessionDep, redis: RedisDep, current_user: CurrentUserDep
) -> ChatResponse:
    """認証済みユーザーからのチャットメッセージを受け取り、LLM応答を生成して返す。"""
    conversation, message = await ChatService(session, redis).send_message(
        user_id=current_user.id,
        conversation_id=payload.conversation_id,
        message=payload.message,
        # is_superuserのユーザーはレート制限の対象外とする
        bypass_rate_limit=current_user.is_superuser,
    )

    return ChatResponse(
        conversation_id=conversation.id, message=MessageRead.model_validate(message)
    )
