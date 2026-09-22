from fastapi import APIRouter

from app.api.deps import CurrentUserDep
from app.schemas.user import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUserDep) -> UserRead:
    """アクセストークンから解決した、現在ログイン中のユーザー情報を返す。"""
    return UserRead.model_validate(current_user)
