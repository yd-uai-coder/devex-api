"""簡易ドキュメントモードの実装手順書のテスト用の文書(実装計画書・内部設計書の Markdown)。

`app/services/doc_generator_service.py`のプロンプトが指示する書式で書く。内部設計書の DF と
モジュール一覧に、実装計画書の WBS の処理とモジュールをそろえる。
"""

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_detailed_project

from app.detailed_design.procedure_basis import procedure_basis
from app.detailed_design.procedure_doc_drafting import (
    GeneratedSimpleFinding,
    GeneratedTestPoint,
    GeneratedUnitFile,
    SimpleProcedureDocGenerationOutput,
)
from app.detailed_design.procedure_output import ProcedureOutputSource, procedure_output_source
from app.detailed_design.stages import StageState
from app.detailed_design.validation import StageSources, validate_procedure_doc
from app.models.project import Project
from app.repositories.generated_document import GeneratedDocumentRepository

PLAN_MD = """# 4. 実装計画書

## 4.1 開発フェーズ分割・マイルストーン
### フェーズ1(M-01〜M-02) ── 【Must】

## 4.2 タスク分解（WBS）
### M-01: 予約の登録 ── 【Must】
- ゴール: 備品を予約できる
- [ ] M-01-T01 [基盤] 開発環境と DB を用意する
  - 処理: なし
  - 依存: なし
  - モジュール: app/main.py
  - 環境・設定: docker-compose.yml, .env.example
- [ ] M-01-T02 [機能] 予約を登録する
  - 処理: DF-1
  - 依存: M-01-T01
  - モジュール: `app/api/reservations.py`, app/services/reservation.py
  - 環境・設定: なし

### **M-02**：予約の一覧 ── 【Should】
- ゴール: 自分の予約を一覧で確かめられる
- [ ] M-02-T01 [機能] 予約の一覧を返す
  - 処理: DF-2
  - 依存: M-01-T02
  - モジュール: app/api/reservations.py

## 4.3 開発環境・CI/CD・事前準備事項
- Docker Compose で API と DB を起動する

## 4.4 想定リスクと対策（トレードオフ・後回し候補）
- **リスク1: 期間の重なり**
"""

# Phase 31 より前の書式(番号の無い層別のチェックボックス)
OLD_PLAN_MD = """# 4. 実装計画書

## 4.2 タスク分解（WBS案）
### バックエンド開発
- [ ] 予約 API を作る
- [ ] 予約の一覧 API を作る
"""

INTERNAL_DESIGN_MD = """# 3. 内部設計書

## 3.1 技術スタック選定・アーキテクチャ方針
- FastAPI と PostgreSQL

## 3.2 データモデル定義

### テーブル: reservations
| カラム名 | データ型 | 制約 | 説明 |
|---|---|---|---|
| id | UUID | PK | 予約ID |

## 3.3 バックエンド処理・モジュール設計
| メソッド | パス | 概要 |
|---|---|---|
| POST | /api/v1/reservations | 予約を登録する(ルート → サービス) |
| GET | /api/v1/reservations | 予約の一覧を返す |

### モジュール一覧
| パス | 層 | 責務 | 主な依存先 |
|---|---|---|---|
| app/main.py | 起動 | アプリの組み立て | app/api/reservations.py |
| `app/api/reservations.py` | api | 予約のルート | app/services/reservation.py |
| app/services/reservation.py | service | 予約の検証と保存 | — |

### 処理別データフロー

#### DF-1: POST /api/v1/reservations
| 元 | データ | 変換 | 先 |
|---|---|---|---|
| 利用者 | 予約リクエスト | 検証して保存 | reservations |
- データ項目: 予約リクエスト(item_id, start_at)

#### DF-2: GET /api/v1/reservations
| 元 | データ | 変換 | 先 |
|---|---|---|---|
| reservations | 予約 | 利用者で絞る | 利用者 |

## 3.4 例外処理・エラーハンドリング・ログ設計
- エラーは {code, message} の形で返す
- ログは JSON で出す
"""

EXTERNAL_DESIGN_MD = """# 2. 外部設計書

## 2.6 API一覧
| メソッド | パス | 概要 | 関連画面 |
|---|---|---|---|
| POST | /api/v1/reservations | 予約を登録する | SCR-001 |
"""

# 簡易モードの内部設計書によくある形: モジュール一覧は層ごとにまとめた行、DF の表の先は
# 「データベース (`<テーブル>` テーブル)」
LAYERED_INTERNAL_DESIGN_MD = INTERNAL_DESIGN_MD.replace(
    """| app/main.py | 起動 | アプリの組み立て | app/api/reservations.py |
| `app/api/reservations.py` | api | 予約のルート | app/services/reservation.py |
| app/services/reservation.py | service | 予約の検証と保存 | — |""",
    """| `app/api/*.py` | ルーター | リクエストの受け付け | Services |
| `app/services/*.py` | サービス | 業務ロジック | Repositories |
| `app/models/{reservation,item}.py` | モデル | テーブル定義 | SQLAlchemy |""",
).replace(
    "| 利用者 | 予約リクエスト | 検証して保存 | reservations |",
    "| 利用者 | 予約リクエスト | 検証して保存 | データベース (`reservations` テーブル) |",
)

REQUIREMENTS_MD = """# 1. 要件定義書

## 1.4 機能要件（MoSCoW優先度）
- **Must have（必須機能）**: 予約の登録
- **Could have（あると良い機能）**: 通知
"""


def simple_documents(**overrides: str) -> dict[str, str]:
    """簡易モードの4文書(文書の種類 → 本文)。`overrides`で1つずつ差し替える。"""
    documents = {
        "requirements": REQUIREMENTS_MD,
        "external_design": EXTERNAL_DESIGN_MD,
        "internal_design": INTERNAL_DESIGN_MD,
        "implementation_plan": PLAN_MD,
    }
    return {**documents, **overrides}


def simple_procedure_doc_model(*, module: str = "app/api/reservations.py") -> dict:
    """`PLAN_MD`の機能の単位 M-01-T02 の手順書(簡易モードの段階8。検証のエラーにならない)。
    AI の指摘は直す先が内部設計書の1件。`module`をモジュール一覧に無いパスにすると警告
    (UNKNOWN_FILE)になる。"""
    return {
        "units": [
            {
                "unit_id": "M-01-T02",
                "title": "予約を登録する",
                "purpose": "予約を登録できるようにする",
                "files": [{"path": module, "kind": "module", "responsibility": "予約の API"}],
                "tests": [
                    {
                        "viewpoint": "予約を登録できる",
                        "sut": "POST /api/v1/reservations",
                        "driver": "API を呼ぶテスト",
                        "stub": "スタブ不要",
                    }
                ],
                "verify": ["テストが通る"],
                "findings": [
                    {
                        "level": "critical",
                        "target": "DF-1",
                        "message": "期間が重なったときの応答が無い",
                        "fix_document": "internal_design",
                    }
                ],
            }
        ]
    }


def sample_simple_procedure_source(*, state: StageState = "approved") -> ProcedureOutputSource:
    """簡易モードの手順書の出力の入力(4文書は`simple_documents()`、段階8は
    `simple_procedure_doc_model()`、検証の指摘は実際の検証の結果)。"""
    documents = simple_documents()
    model = simple_procedure_doc_model()
    issues = validate_procedure_doc(model, StageSources(documents=documents, mode="simple"))
    return procedure_output_source(
        "予約システム",
        state,
        procedure_basis("simple", {}, documents),
        model,
        issues,
        documents["requirements"],
    )


async def create_simple_procedure_project(
    session: AsyncSession, **overrides: str
) -> Project:
    """簡易モードのプロジェクト(4文書`simple_documents()`を1版ずつ持ち、段階8が開いている)。"""
    project = await create_detailed_project(session, mode="simple", with_documents=False)
    documents = GeneratedDocumentRepository(session)
    for doc_type, content in simple_documents(**overrides).items():
        await documents.create_version(project_id=project.id, doc_type=doc_type, content=content)
    await session.commit()
    return project


def simple_procedure_output() -> SimpleProcedureDocGenerationOutput:
    """簡易モードの手順書1つ分の構造化出力(FakeLLM が返す)。M-01-T02 のモジュールを書き、
    直す先が内部設計書の指摘を1件持つ。"""
    return SimpleProcedureDocGenerationOutput(
        purpose="予約を登録できる",
        files=[
            GeneratedUnitFile(
                path="app/api/reservations.py",
                kind="module",
                responsibility="予約の API",
                basis="内部設計書 3.3",
            )
        ],
        notes=[],
        tests=[
            GeneratedTestPoint(viewpoint="登録できる", sut="POST", driver="結合", stub="スタブ不要")
        ],
        gwt=[],
        verify=["テストが通る"],
        findings=[
            GeneratedSimpleFinding(
                level="major",
                target="DF-1",
                message="重なったときの応答が無い",
                fix_document="internal_design",
            )
        ],
    )
