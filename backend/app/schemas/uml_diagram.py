import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.uml.domain import NotationType, SemanticModel
from app.uml.layout.model import LayoutModel


class UmlDiagramRead(BaseModel):
    """UML図1件をAPIレスポンスとして返す際のスキーマ。`semantic_model`/`layout_model`は
    生dictではなくapp.uml.domain/app.uml.layoutの型付きモデルをそのまま使う(既存の
    project.intake等の「生dict」パターンとは異なる新規パターン。Pydanticネイティブに
    構造を検証できるため)。`layout_model`は`POST .../layout`実行前、
    またはAIで再生成した直後はNone。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    view: str
    notation: NotationType
    # subject: 同じ記法の中で図を識別するキー(component: ''、ER: ''またはグループ名、DFD: 処理名)
    subject: str
    # scope: AIに渡した対象の選択(ER部分図の{"tables": [...]})
    scope: dict | None
    semantic_model: SemanticModel
    layout_model: LayoutModel | None
    status: str
    version: int
    # generation_status: 'generating'/'completed'/'failed'(FEはgeneratingの間ポーリングする)
    generation_status: str
    generation_error: str | None
    source_doc_versions: dict | None
    created_at: datetime
    updated_at: datetime


class UmlDiagramUpdate(BaseModel):
    """UML図の全体更新リクエストのスキーマ。`version`は楽観ロック用
    (更新対象を最後に取得した時点のUmlDiagramRead.versionをそのまま返す想定)。"""

    version: int
    semantic_model: SemanticModel
