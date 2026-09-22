from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.error_handlers import register_error_handlers
from app.api.middleware import BodySizeLimitMiddleware
from app.api.routes import api_router
from app.core.config import settings
from app.core.database import engine
from app.infrastructure.redis import get_redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """アプリケーション終了時にDBエンジンの接続プールを解放する。"""
    yield
    await engine.dispose()


# 本番環境ではSwagger UI/ReDoc/OpenAPIスキーマを非公開にする
_docs_enabled = settings.ENVIRONMENT != "production"

app = FastAPI(
    title=settings.PROJECT_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)

register_error_handlers(app)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/")
async def root() -> dict[str, str]:
    """疎通確認用のルートエンドポイント。"""
    return {"name": settings.PROJECT_NAME, "status": "ok"}


@app.get("/health")
async def health() -> dict[str, object]:
    """DBとRedisへの接続確認結果をもとに、アプリ全体の稼働状況を返す。"""
    db_ok = await _check_database()
    redis_ok = await _check_redis()
    return {
        "status": "ok" if db_ok and redis_ok else "degraded",
        "database": "ok" if db_ok else "unavailable",
        "redis": "ok" if redis_ok else "unavailable",
    }


async def _check_database() -> bool:
    """DBへの簡易クエリ実行に成功するかどうかを確認する。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        # 失敗要因の種類は問わず、稼働状況の判定にのみ使う
        return False


async def _check_redis() -> bool:
    """Redisへのpingに成功するかどうかを確認する。"""
    client = get_redis_client()
    try:
        return bool(await client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()
