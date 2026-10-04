"""詳細設計モードのテストで使うプロジェクトの組み立て(段階のサービス・ルートのテストで共有する)。"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.data_item_service import DataItemService
from app.services.design_stage_service import DesignStageService


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
