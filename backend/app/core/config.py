from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 本番で許す JWT 秘密鍵の最小長(HS256 の鍵として 256 bit = 32 バイト以上が目安)
_JWT_SECRET_MIN_LENGTH = 32


class Settings(BaseSettings):
    """環境変数・.envファイルから読み込むアプリケーション全体の設定値。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # App
    PROJECT_NAME: str = "FastAPI LangChain Template"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Database
    DATABASE_URL: str

    # Redis
    REDIS_URL: str

    # JWT
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # AI
    GOOGLE_API_KEY: str | None = None
    TAVILY_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # LLM呼び出しの最大待機秒数(langchain-google-genaiのChatGoogleGenerativeAI
    # が受け取るtimeout秒。
    # 既定はNone=無制限で、応答がハングした場合クライアントへ何も返せないまま無限に待ち続ける恐れがある。
    LLM_TIMEOUT_SECONDS: float = 60.0

    # trueの場合、get_gemini_llm()は実際のGemini APIを呼ばず、
    # app/ai/llm/fake.pyの決定論的なE2eFakeLLMを返す
    # 既定はfalseで、次のバリデータによりENVIRONMENT=production下でtrueにすることは起動時に拒否される。
    E2E_FAKE_LLM: bool = False

    # Rate limit（チャットメッセージ送信のレート制限。単位時間あたりの上限回数）
    CHAT_RATE_LIMIT_PER_HOUR: int = 20
    CHAT_RATE_LIMIT_PER_DAY: int = 100

    # 認証エンドポイントのレート制限(総当たり・大量登録の対策。単位時間あたりの上限回数)。
    # IP 単位は NAT 配下の複数人が巻き込まれ得るため、メール単位より緩くする。
    LOGIN_RATE_LIMIT_PER_IP_PER_HOUR: int = 30
    LOGIN_RATE_LIMIT_PER_EMAIL_PER_HOUR: int = 20
    REGISTER_RATE_LIMIT_PER_IP_PER_HOUR: int = 10

    # リクエストボディの上限(バイト)。nginx の client_max_body_size と同じ値に揃える。
    MAX_REQUEST_BODY_BYTES: int = 2_000_000

    @model_validator(mode="after")
    def _reject_unsafe_production_settings(self) -> Self:
        """`ENVIRONMENT=production` で起動してはいけない設定を、起動時(設定の読み込み時)に弾く。
        設定ミスは「動くが危険」な状態になりやすいため、実行時でなく起動時に落とす。
        
        E2E_FAKE_LLM=trueは本番で絶対に有効化してはならない(実際にはAIが応答していないのに
        応答しているように見せかけるため、事故時の実害が大きい)。DEBUG・JWT_SECRET_KEYと
        同じ仕組みでチェックする。"""

        if self.ENVIRONMENT != "production":
            return self
        problems: list[str] = []
        if self.DEBUG:
            problems.append("DEBUG=true(SQL のログ出力などで機密が漏れる)")
        secret = self.JWT_SECRET_KEY
        if len(secret) < _JWT_SECRET_MIN_LENGTH or secret.lower().startswith("change-me"):
            problems.append(
                f"JWT_SECRET_KEY が短い(<{_JWT_SECRET_MIN_LENGTH} 文字)か、"
                ".env.example のプレースホルダのまま"
            )
        if self.E2E_FAKE_LLM:
            problems.append("E2E_FAKE_LLM=true(E2Eテスト専用フラグ。本番では常にfalseにすること)")
        if problems:
            raise ValueError(
                "本番(ENVIRONMENT=production)で許されない設定: " + " / ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Settingsインスタンスを生成する。lru_cacheによりプロセス内では1回だけ生成される。"""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
