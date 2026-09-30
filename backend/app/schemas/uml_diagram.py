import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.uml.domain import DiagramStatus, NotationType, SemanticModel
from app.uml.layout.model import LayoutModel
from app.uml.sync import DocState


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
    # status: レビューの状態(draft→reviewing→approved→exported。app/uml/domain/status.py)
    status: DiagramStatus
    version: int
    # generation_status: 'generating'/'completed'/'failed'(FEはgeneratingの間ポーリングする)
    generation_status: str
    generation_error: str | None
    source_doc_versions: dict | None
    created_at: datetime
    updated_at: datetime


class UmlDiagramUpdate(BaseModel):
    """UML図の全体更新リクエストのスキーマ。`version`は楽観ロック用
    (更新対象を最後に取得した時点のUmlDiagramRead.versionをそのまま返す想定)。
    `layout_model`はレビュー画面で手動移動した座標を意味モデルと同じ保存・同じversionで
    保存するための任意項目。省略した場合は保存済みの配置を保つ(M6: 手動座標は自動レイアウト
    以外では上書きしない)。"""

    version: int
    semantic_model: SemanticModel
    layout_model: LayoutModel | None = None


class UmlDiagramApprove(BaseModel):
    """UML図の承認リクエストのスキーマ。`version`は利用者が画面で見ていた版
    (見ていない版を承認しないための楽観ロック。一致しなければ409)。"""

    version: int


class UmlReflectRead(BaseModel):
    """図の一括再反映の結果(反映した承認済みの図の数)。"""

    reflected: int


class UmlEmbedRead(BaseModel):
    """文書のプレビューに差し込む図1枚分と、図と文書の食い違い(陳腐化)。

    - `source_outdated`: 図を生成した後に内部設計書が再生成・復元された(図が古い)
    - `doc_state`: 文書に反映した内容と図の今の状態の関係(app/uml/sync/staleness.py参照)
    - `svg`: 承認済みの図だけ(プレビューではimgのdata URIとして表示し、スクリプトを実行させない)
    """

    diagram_id: uuid.UUID
    notation: NotationType
    subject: str
    title: str
    status: DiagramStatus
    version: int
    source_outdated: bool
    doc_state: DocState
    svg: str | None
