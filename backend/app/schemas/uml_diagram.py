import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.uml.domain import NotationType, SemanticModel


class UmlDiagramRead(BaseModel):
    """UML図1件をAPIレスポンスとして返す際のスキーマ。`semantic_model`は生dictではなく
    app.uml.domainの型付きdiscriminated unionをそのまま使う(既存のproject.intake等の
    「生dict」パターンとは異なる新規パターン。Pydanticネイティブに構造を検証できるため)。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    view: str
    notation: NotationType
    semantic_model: SemanticModel
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class UmlDiagramCreate(BaseModel):
    """UML図の生成トリガーリクエストのスキーマ。Phase 8時点ではnotationのみを受け取り、
    要素・関係が空のdraftを作成する(実AI生成はPhase 10で追加)。"""

    notation: NotationType


class UmlDiagramUpdate(BaseModel):
    """UML図の全体更新リクエストのスキーマ。`version`は楽観ロック用
    (更新対象を最後に取得した時点のUmlDiagramRead.versionをそのまま返す想定)。"""

    version: int
    semantic_model: SemanticModel
