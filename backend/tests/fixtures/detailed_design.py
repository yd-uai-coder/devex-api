"""詳細設計モードのテストで使うプロジェクトの組み立て(段階のサービス・ルートのテストで共有する)。"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.detailed_design.document import (
    DataItemEntry,
    DocumentSource,
    RenderedDiagram,
    document_source,
)
from app.detailed_design.procedure_doc_drafting import (
    GeneratedFinding,
    GeneratedTestPoint,
    GeneratedUnitFile,
    ProcedureDocGenerationOutput,
)
from app.detailed_design.stages import StageState
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.data_item_service import DataItemService
from app.services.design_stage_service import DesignStageService
from app.uml.domain import SemanticModelAdapter
from app.uml.domain.er import ErSemanticModel
from app.uml.layout import compute_layout, edge_labels


async def create_detailed_project(
    session: AsyncSession, *, mode: str = "detailed", with_documents: bool = True
) -> Project:
    """詳細設計モードのプロジェクトを作る。`with_documents`なら要件定義・外部設計を1版ずつ持つ。"""
    user = User(email=f"owner-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode=mode, status="completed")
    session.add(project)
    await session.flush()
    if with_documents:
        documents = GeneratedDocumentRepository(session)
        for doc_type in ("requirements", "external_design"):
            await documents.create_version(
                project_id=project.id, doc_type=doc_type, content=f"# {doc_type}"
            )
    await session.commit()
    return project


def function_list_model(*, group: str = "reservations") -> dict:
    """段階1の検証を通る最小の機能一覧(処理1件・機能グループ1つ)。`group`を一覧に無い名前に
    すると、検証のエラー(UNKNOWN_GROUP)になる。"""
    return {
        "groups": ["reservations"],
        "functions": [
            {
                "id": "F-01",
                "name": "予約を登録する",
                "kind": "API",
                "trigger": "POST /api/v1/reservations",
                "screens": ["SCR-001"],
                "group_initial": "reservations",
                "group": group,
                "summary": "予約を保存する",
            }
        ],
        "next_number": 2,
    }


def data_flow_model(*, dfd_groups: list[str] | None = None) -> dict:
    """`function_list_model()`の処理1件に対応する、段階2の検証を通る処理概要表(DFD を描く
    グループは既定で無し)。`dfd_groups`を渡すと、そのグループの DFD が要るようになる。"""
    return {
        "dfd_groups": dfd_groups or [],
        "summaries": [
            {
                "function_id": "F-01",
                "input": "予約の内容",
                "process": "重複を確かめて保存する",
                "output": "予約",
            }
        ],
    }


def er_model(*, tables: tuple[str, ...] = ("reservations",)) -> dict:
    """ER の意味モデル(テーブルごとに主キーの列 id だけ)。段階3の検証・生成のテストで使う。"""
    return {
        "notation": "er",
        "elements": [
            {
                "id": f"t-{name}",
                "name": name,
                "kind": "table",
                "columns": [{"name": "id", "type": "UUID", "is_primary_key": True}],
            }
            for name in tables
        ],
        "relations": [],
    }


def crud_model(*, ops: str = "C", draft: bool = False) -> dict:
    """`function_list_model()`の F-01 が reservations に書く、段階3の CRUD 図(セル1つ)。"""
    return {"cells": [{"function_id": "F-01", "table": "reservations", "ops": ops, "draft": draft}]}


def component_model(*, layers: tuple[str, ...] = ("api", "service")) -> dict:
    """構成図の意味モデル(層ごとにモジュール1つ、上の層が下の層に依存する)。段階4の検証・生成の
    テストで使う。"""
    elements = [
        {"id": f"m-{layer}", "name": f"{layer}s", "kind": "module", "layer": layer}
        for layer in layers
    ]
    relations = [
        {"id": f"d{i}", "source_id": a["id"], "target_id": b["id"]}
        for i, (a, b) in enumerate(zip(elements, elements[1:], strict=False), start=1)
    ]
    return {"notation": "component", "elements": elements, "relations": relations}


def module_list_model(*, functions: list[str] | None = None, layer: str = "api") -> dict:
    """`function_list_model()`の F-01 に関わる、段階4の検証を通るモジュール一覧(行1つ)。
    `functions`に機能一覧に無い処理IDを渡すと、検証のエラー(UNKNOWN_FUNCTION)になる。"""
    return {
        "modules": [
            {
                "path": "app/api/routes/reservations.py",
                "layer": layer,
                "responsibility": "予約の API",
                "depends_on": [],
                "functions": ["F-01"] if functions is None else functions,
                "all_functions": False,
            }
        ]
    }


def group_dfd_model(data_item_id: uuid.UUID) -> dict:
    """機能グループ reservations の DFD(利用者 → F-01 → reservations。F-01 が書き込む)。"""
    return {
        "notation": "dfd",
        "elements": [
            {"id": "e1", "name": "利用者", "element_type": "external_entity"},
            {"id": "F-01", "name": "F-01 予約を登録する", "element_type": "process"},
            {"id": "s1", "name": "reservations", "element_type": "data_store"},
        ],
        "relations": [
            {"id": "f1", "source_id": "e1", "target_id": "F-01", "data_item_id": str(data_item_id)},
            {"id": "f2", "source_id": "F-01", "target_id": "s1", "data_item_id": str(data_item_id)},
        ],
    }


async def create_stage3_project(session: AsyncSession) -> Project:
    """段階1・2を承認したプロジェクト(段階3が開いている)。段階2は機能グループ reservations の
    DFD(承認済み)を1枚持ち、データ辞書に「予約」がある。"""
    project = await create_detailed_project(session)
    stages = DesignStageService(session)
    await stages.save(project, stage=1, expected_version=None, model=function_list_model())
    await stages.approve(project, stage=1, expected_version=1)
    item = await DataItemService(session).create(
        project_id=project.id, name="予約", fields=[{"name": "id", "type": "UUID"}]
    )
    diagram = await UmlDiagramRepository(session).create(
        project_id=project.id,
        view="dataflow",
        notation="dfd",
        semantic_model=group_dfd_model(item.id),
        subject="reservations",
    )
    diagram.status = "approved"
    await session.commit()
    model = data_flow_model(dfd_groups=["reservations"])
    await stages.save(project, stage=2, expected_version=None, model=model)
    await stages.approve(project, stage=2, expected_version=1)
    return project


async def create_stage4_project(session: AsyncSession) -> Project:
    """段階1〜3を承認したプロジェクト(段階4が開いている)。段階3は ER(reservations。承認済み)と、
    F-01 が reservations に書く CRUD 図を持つ。"""
    project = await create_stage3_project(session)
    diagram = await UmlDiagramRepository(session).create(
        project_id=project.id, view="data", notation="er", semantic_model=er_model(), subject=""
    )
    diagram.status = "approved"
    await session.commit()
    stages = DesignStageService(session)
    await stages.save(project, stage=3, expected_version=None, model=crud_model())
    await stages.approve(project, stage=3, expected_version=1)
    return project


def procedure_model(
    *, callee: str = "app/api/routes/reservations.py", reason: str = "検証"
) -> dict:
    """`function_list_model()`の F-01 の手順(利用者 → `callee`、分岐1つ)。段階5の検証を通る。
    `callee`をモジュール一覧に無いパスにすると、検証のエラー(UNKNOWN_CALLEE)になる。"""
    return {
        "procedures": [
            {
                "function_id": "F-01",
                "reason": reason,
                "note": "",
                "steps": [
                    {
                        "caller": "利用者",
                        "callee": callee,
                        "call": "create_reservation",
                        "data": "予約リクエスト",
                        "action": "本文を検証する",
                        "result": "予約",
                        "db": "reservations C",
                        "branch": "1a へ",
                    },
                    {"action": "本文が不正", "branch": "422", "is_branch": True},
                ],
            }
        ]
    }


async def create_stage5_project(session: AsyncSession) -> Project:
    """段階1〜4を承認したプロジェクト(段階5が開いている)。段階4は構成図(承認済み)と、
    モジュール app/api/routes/reservations.py の1行を持つ。"""
    project = await create_stage4_project(session)
    diagram = await UmlDiagramRepository(session).create(
        project_id=project.id,
        view="structure",
        notation="component",
        semantic_model=component_model(),
        subject="",
    )
    diagram.status = "approved"
    await session.commit()
    stages = DesignStageService(session)
    await stages.save(project, stage=4, expected_version=None, model=module_list_model())
    await stages.approve(project, stage=4, expected_version=1)
    return project


def logic_model(
    *,
    module: str = "app/api/routes/reservations.py",
    function: str = "create_reservation",
    pre: str = "利用者は認証済み",
) -> dict:
    """`procedure_model()`の手順 F-01#1 が呼ぶ関数1つの詳細。段階6の検証を通る。
    `function`を手順に無い名前にすると、検証のエラー(UNCALLED_LOGIC)になる。"""
    return {
        "logics": [
            {
                "module": module,
                "function": function,
                "signature": "async def create_reservation(payload) -> Reservation",
                "args": "payload: 予約リクエスト",
                "returns": "保存済みの予約",
                "raises": "ValidationError(422)",
                "pre": pre,
                "post": "予約が1件増える",
                "pseudo": [{"text": "本文を検証する", "sub": ["不正なら 422"]}],
            }
        ]
    }


def plan_model(
    *,
    function_ids: list[str] | None = None,
    module: str = "app/api/routes/reservations.py",
) -> dict:
    """段階7の検証を通る横断事項と実装計画(既定の横断事項4項目・マイルストーン1つ・リスク1件)。

    マイルストーンには、基盤の単位 M-01-T01(環境・設定のファイル`Dockerfile`)と、それに依存する
    機能の単位 M-01-T02(F-01、モジュール`module`)を置く。`function_list_model()`の F-01 と
    `module_list_model()`のパスを参照する。`function_ids`に機能一覧に無い処理IDを、`module`に
    モジュール一覧に無いパスを渡すと、検証のエラーになる。"""
    return {
        "crosscutting": [
            {
                "topic": "例外と HTTP",
                "policy": "ドメイン例外を共通の形に変換する",
                "modules": ["app/api/routes/reservations.py"],
            },
            {"topic": "認証", "policy": "JWT で利用者を確かめる", "modules": []},
            {"topic": "トランザクション", "policy": "commit はサービスだけ", "modules": []},
            {"topic": "ログ", "policy": "JSON で出す", "modules": []},
        ],
        "milestones": [
            {
                "name": "予約の登録",
                "goal": "予約を登録できる",
                "priority": "Must",
                "tasks": [
                    {
                        "kind": "base",
                        "title": "開発環境を用意する",
                        "function_ids": [],
                        "depends_on": [],
                        "modules": [],
                        "config_files": ["Dockerfile"],
                    },
                    {
                        "kind": "feature",
                        "title": "予約を登録する",
                        "function_ids": ["F-01"] if function_ids is None else function_ids,
                        "depends_on": ["M-01-T01"],
                        "modules": [module],
                        "config_files": [],
                    },
                ],
            }
        ],
        "environment": "Python 3.13 と PostgreSQL",
        "risks": [{"risk": "予約の重複", "mitigation": "一意制約で防ぐ"}],
    }


async def create_stage6_project(session: AsyncSession) -> Project:
    """段階1〜5を承認したプロジェクト(段階6が開いている)。段階5は F-01 の手順(`procedure_model()`。
    手順 F-01#1 が app/api/routes/reservations.py の create_reservation を呼ぶ)を持つ。"""
    project = await create_stage5_project(session)
    stages = DesignStageService(session)
    await stages.save(project, stage=5, expected_version=None, model=procedure_model())
    await stages.approve(project, stage=5, expected_version=1)
    return project


def document_stage_models(*, dfd_groups: list[str] | None = None) -> dict[int, dict]:
    """段階1〜7の、組み立ての入力になる内容(すべて検証を通る)。段階5の手順 F-01#1 が、段階6の
    関数(app/api/routes/reservations.py の create_reservation)を呼ぶ。段階7は`plan_model()`。
    詳細設計書の組み立てのテストで使う。"""
    return {
        1: function_list_model(),
        2: data_flow_model(dfd_groups=dfd_groups),
        3: crud_model(),
        4: module_list_model(),
        5: procedure_model(),
        6: logic_model(),
        7: plan_model(),
    }


ALL_APPROVED: dict[int, StageState] = dict.fromkeys(range(1, 8), "approved")
DOCUMENT_DATA_ITEM_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def sample_document_source(
    *, states: dict[int, StageState] | None = None, models: dict[int, dict] | None = None
) -> DocumentSource:
    """全章がそろう詳細設計書の入力(図は描画済みの小さな SVG)。md・HTML の組み立てのテストで
    使う。`states`・`models`で、未承認の章や省略の段階6を作れる。"""
    return document_source(
        "予約システム",
        ALL_APPROVED if states is None else states,
        document_stage_models(dfd_groups=["reservations"]) if models is None else models,
        dfd_diagrams={
            "reservations": RenderedDiagram(
                title="データフロー図: reservations",
                path="diagrams/dfd_reservations.svg",
                svg="<svg/>",
            )
        },
        dfd_models=(group_dfd_model(DOCUMENT_DATA_ITEM_ID),),
        data_items=(
            DataItemEntry(id=str(DOCUMENT_DATA_ITEM_ID), name="予約", fields=("id", "starts_at")),
        ),
        er=ErSemanticModel.model_validate(er_model()),
        er_diagram=RenderedDiagram(title="ER図(全体)", path="diagrams/er.svg", svg="<svg/>"),
        component_diagram=RenderedDiagram(
            title="コンポーネント図(全体)", path="diagrams/component.svg", svg="<svg/>"
        ),
    )


async def create_stage7_project(session: AsyncSession) -> Project:
    """段階1〜6を承認し、図(DFD・ER・構成図)に配置を持たせたプロジェクト(段階7が開いている)。
    図は段階のテスト用に配置なしで承認済みにしてあるので、出力できるよう配置を足す
    (図のサービスの自動レイアウトは承認を差し戻すため使わず、レイアウトエンジンを直接呼ぶ)。"""
    project = await create_stage6_project(session)
    stages = DesignStageService(session)
    await stages.save(project, stage=6, expected_version=None, model=logic_model())
    await stages.approve(project, stage=6, expected_version=1)
    items = await DataItemService(session).list_for_project(project.id)
    names = {item.id: item.name for item in items}
    for diagram in await UmlDiagramRepository(session).list_for_project(project.id):
        model = SemanticModelAdapter.validate_python(diagram.semantic_model)
        layout = compute_layout(str(diagram.id), model, edge_labels(model, names))
        diagram.layout_model = layout.model_dump(mode="json")
    await session.commit()
    return project


async def create_document_project(session: AsyncSession) -> Project:
    """段階1〜7を承認したプロジェクト(詳細設計書を組み立てると全章がそろい、実装計画もある)。
    段階7は`plan_model()`。"""
    project = await create_stage7_project(session)
    stages = DesignStageService(session)
    await stages.save(project, stage=7, expected_version=None, model=plan_model())
    await stages.approve(project, stage=7, expected_version=1)
    return project


def procedure_doc_model(
    *,
    unit_id: str = "M-01-T02",
    title: str = "予約を登録する",
    module: str = "app/api/routes/reservations.py",
) -> dict:
    """`plan_model()`の機能の単位 M-01-T02 の手順書(段階8。検証を通る)。ファイルはモジュール
    `module`とテスト1本。`unit_id`・`title`を段階7と違うものにすると検証のエラー(UNIT_MISMATCH)、
    `module`をモジュール一覧に無いパスにすると警告(UNKNOWN_FILE)になる。"""
    return {
        "units": [
            {
                "unit_id": unit_id,
                "title": title,
                "purpose": "予約を登録できるようにする",
                "files": [
                    {"path": module, "kind": "module", "responsibility": "予約の API"},
                    {"path": "tests/test_reservations.py", "kind": "test"},
                ],
                "notes": [],
                "tests": [
                    {
                        "viewpoint": "予約を登録できる",
                        "sut": "create_reservation",
                        "driver": "API を呼ぶテスト",
                        "stub": "スタブ不要",
                    }
                ],
                "gwt": [],
                "verify": ["テストが通る"],
                "findings": [
                    {
                        "level": "critical",
                        "target": "07章 例外と HTTP",
                        "message": "重複したときの応答が無い",
                        "fix_stage": 7,
                    }
                ],
            }
        ]
    }


def procedure_doc_output(**overrides) -> ProcedureDocGenerationOutput:
    """段階8の手順書1つ分の構造化出力(FakeLLM が返す)。`plan_model()`の M-01-T02 のモジュールを
    書き、最重要の指摘を1件持つ。`overrides`で欄を差し替える。"""
    values = {
        "purpose": "予約を登録できる",
        "files": [
            GeneratedUnitFile(
                path="app/api/routes/reservations.py",
                kind="module",
                responsibility="予約の API",
                basis="段階4",
            )
        ],
        "notes": ["マイグレーションを1本足す"],
        "tests": [
            GeneratedTestPoint(viewpoint="登録できる", sut="POST", driver="結合", stub="スタブ不要")
        ],
        "gwt": ["Given 未登録 / When 登録 / Then 1件増える"],
        "verify": ["テストが通る"],
        "findings": [
            GeneratedFinding(level="critical", target="段階3", message="項目が無い", fix_stage=3)
        ],
    }
    return ProcedureDocGenerationOutput(**(values | overrides))
