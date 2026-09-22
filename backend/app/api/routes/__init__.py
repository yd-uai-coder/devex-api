from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.users import router as users_router

# 各機能別ルーターを1つのAPIRouterに集約し、main.pyから一括でincludeできるようにする
api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(chat_router)
