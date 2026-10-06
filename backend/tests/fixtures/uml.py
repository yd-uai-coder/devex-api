"""UML図の生成・検証まわりのテストで共有するフィクスチャ。

- 固定形式の見出し(3.2のテーブル見出し・処理別データフローのDF見出し)を持つ内部設計書のサンプル
- プロジェクト・空の図の作成ヘルパー(空の図はリポジトリで直接作る)
- 記法ごとのLLM出力スキーマのサンプル(FakeLLMの構造化出力として渡す)
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.uml_diagram import UmlDiagram
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.uml_diagram_service import UmlDiagramService
from app.uml.domain import (
    NOTATION_TO_VIEW,
    NotationType,
    SemanticModelAdapter,
    empty_semantic_model,
)
from app.uml.generation.schemas import (
    ComponentGenerationOutput,
    DfdGenerationOutput,
    GeneratedDataItem,
    GeneratedDataItemField,
    GeneratedDependency,
    GeneratedFlow,
    GeneratedModule,
    GeneratedNode,
    GeneratedProcess,
)

INTERNAL_DESIGN_MD = """# 3. 内部設計書

## 3.1 技術スタック選定・アーキテクチャ方針
- FastAPI の api / service / repository の3層構成

## 3.2 データモデル定義
- 主要エンティティ: users, reservations

### テーブル: users
| カラム名 | データ型 | 制約 | 説明 |
|---|---|---|---|
| id | UUID | PK | ユーザーID |

### テーブル: reservations
| カラム名 | データ型 | 制約 | 説明 |
|---|---|---|---|
| id | UUID | PK | 予約ID |
| user_id | UUID | FK | 予約者 |

## 3.3 バックエンド処理・モジュール設計
- 主要処理ロジック: api → service → repository

| メソッド | パス | 概要 |
|---|---|---|
| POST | /api/v1/reservations | 予約作成 |

### 処理別データフロー

#### DF-1: POST /api/v1/reservations
| 元 | データ | 変換 | 先 |
|---|---|---|---|
| 利用者 | 予約リクエスト | 検証して保存 | reservations |
- データ項目: 予約リクエスト(item_id, start_at)

#### DF-2: GET /api/v1/reservations
| 元 | データ | 変換 | 先 |
|---|---|---|---|
| reservations | 予約一覧 | 利用者の予約だけ返す | 利用者 |
- データ項目: 予約一覧(id, start_at)

#### DF-3: 予約リマインドバッチ
| 元 | データ | 変換 | 先 |
|---|---|---|---|
| reservations | 予約一覧 | 翌日の予約を抽出 | 通知サービス |
- データ項目: 予約一覧(id, start_at)

## 3.4 例外処理・エラーハンドリング・ログ設計
- 共通エラーレスポンス形式
"""


async def create_project(session: AsyncSession) -> Project:
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="備品予約システム")
    session.add(project)
    await session.flush()
    return project


async def create_project_with_internal_design(
    session: AsyncSession, content: str = INTERNAL_DESIGN_MD
) -> Project:
    """内部設計書(現行版)を1件持つプロジェクトを作る。"""
    project = await create_project(session)
    await GeneratedDocumentRepository(session).create_version(
        project_id=project.id, doc_type="internal_design", content=content
    )
    await session.commit()
    return project


async def create_empty_diagram(
    session: AsyncSession, project_id: uuid.UUID, notation: NotationType, subject: str = ""
) -> UmlDiagram:
    """要素・関係が空の図を1枚作る(status='draft', version=1, generation_status='completed')。"""
    diagram = await UmlDiagramRepository(session).create(
        project_id=project_id,
        view=NOTATION_TO_VIEW[notation],
        notation=notation,
        semantic_model=empty_semantic_model(notation).model_dump(mode="json"),
        subject=subject,
    )
    await session.commit()
    return diagram


def component_output() -> ComponentGenerationOutput:
    return ComponentGenerationOutput(
        modules=[
            GeneratedModule(id="m1", name="api", description="ルーター", layer="api"),
            GeneratedModule(id="m2", name="service", description="ユースケース", layer="service"),
        ],
        dependencies=[GeneratedDependency(id="d1", source_id="m1", target_id="m2")],
    )


def dfd_output(data_item_name: str = "予約リクエスト") -> DfdGenerationOutput:
    return DfdGenerationOutput(
        data_items=[
            GeneratedDataItem(
                name=data_item_name,
                fields=[
                    GeneratedDataItemField(name="item_id", type="UUID"),
                    GeneratedDataItemField(name="start_at", type=""),
                ],
            )
        ],
        processes=[
            GeneratedProcess(
                id="p1", name="予約を登録する", description="検証して保存する", layer="service"
            )
        ],
        external_entities=[GeneratedNode(id="e1", name="利用者")],
        data_stores=[GeneratedNode(id="s1", name="reservations")],
        flows=[
            GeneratedFlow(id="f1", source_id="e1", target_id="p1", data_item_name=data_item_name),
            GeneratedFlow(id="f2", source_id="p1", target_id="s1", data_item_name=data_item_name),
        ],
    )


TWO_MODULES = {
    "elements": [
        {"id": "c1", "name": "認証API", "description": "ルーター"},
        {"id": "c2", "name": "認証サービス"},
    ],
    "relations": [{"id": "r1", "source_id": "c1", "target_id": "c2"}],
}


async def create_approved_diagram(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    model: dict | None = None,
    notation: NotationType = "component",
    subject: str = "",
) -> UmlDiagram:
    """意味モデルを保存 → 自動レイアウト → 承認まで済ませた図を返す(version=2、status=approved)。
    内部設計書があれば、承認と同時に反映される。"""
    service = UmlDiagramService(session)
    diagram = await create_empty_diagram(session, project_id, notation, subject)
    semantic_model = SemanticModelAdapter.validate_python(
        {"notation": notation, **(model if model is not None else TWO_MODULES)}
    )
    await service.update(
        project_id=project_id,
        diagram_id=diagram.id,
        expected_version=1,
        semantic_model=semantic_model,
    )
    laid_out = await service.compute_layout(project_id=project_id, diagram_id=diagram.id)
    return await service.approve(
        project_id=project_id, diagram_id=diagram.id, expected_version=laid_out.version
    )
