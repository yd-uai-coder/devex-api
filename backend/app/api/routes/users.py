from fastapi import APIRouter

from app.api.deps import CurrentUserDep, SessionDep
from app.schemas.user import ReviewSettingsUpdate, UserRead
from app.services.user import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUserDep) -> UserRead:
    """アクセストークンから解決した、現在ログイン中のユーザー情報を返す。"""
    return UserRead.model_validate(current_user)


@router.put("/me/review-settings", response_model=UserRead)
async def update_review_settings(
    body: ReviewSettingsUpdate, session: SessionDep, current_user: CurrentUserDep
) -> UserRead:
    """設計レビューのオン・オフを本人が切り替える(superuser 以外は403 REVIEW_NOT_ALLOWED)。"""
    user = await UserService(session).update_review_setting(
        current_user, enabled=body.review_enabled
    )
    return UserRead.model_validate(user)
