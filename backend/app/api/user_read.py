"""ユーザーの応答(UserRead)を、レビューを使えるかの判定を添えて作る。

レビューを使えるかはキーの登録(DB)を読むので、スキーマの計算プロパティでは出せない。ユーザーを
返すルート(登録・自分の情報・ユーザーネーム・メール・レビューの設定)が共通で使う。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.review import review_effective
from app.schemas.user import UserRead
from app.services.llm_resolver import LlmResolver


async def user_read(session: AsyncSession, user: User) -> UserRead:
    """ユーザーの応答。`review_allowed`・`review_effective` を埋める。"""
    allowed = await LlmResolver(session).review_allowed(user)
    return UserRead.model_validate(user).model_copy(
        update={
            "review_allowed": allowed,
            "review_effective": review_effective(
                review_allowed=allowed, review_enabled=user.review_enabled
            ),
        }
    )
