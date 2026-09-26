import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, PortableJSON


class PromptTemplate(Base):
    """プロジェクト作成時に選択できるテンプレート定義を表すORMモデル(SCR-003)。

    default_environment: SCR-004の初期ヒアリング入力(intake.environment)へプリフィルするデフォルト値。
    """

    __tablename__ = "prompt_templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)                       # LLMに与えるシステムプロンプト定義
    default_environment: Mapped[dict | None] = mapped_column(PortableJSON, nullable=True)  #このテンプレート選択時にSCR-004のintake環境設定へプリフィルするデフォルト値。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
