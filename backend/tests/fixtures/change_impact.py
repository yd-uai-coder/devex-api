"""変更の影響のテスト用の設計(段階1〜8 と文書の model)。

予約の登録(F-01)と一覧(F-02)の2処理が、3層のモジュール(入口・サービス・リポジトリ)を共有する。
「処理を軸」のたどり方が別の処理へ広がらないこと(共有のモジュールから広げない)を確かめるため、
最小の fixture(処理1つ)と別に置く。

- 要件: FR-001(登録)・FR-002(一覧)・NFR-001(本人のみ)。画面 SCR-001 は両方の要件を持つ。
- API: POST(F-01 のトリガー)・GET(F-02 のトリガー)。
- 段階5: F-01 は 入口 → サービス register → リポジトリ insert(reservations C)、F-02 は
  入口 → サービス list → リポジトリ select(reservations R)。
- 段階7: 横断事項「認証」(NFR-001・入口)・「トランザクション」(サービス)、単位 U-001(基盤)・
  U-002(F-01)・U-003(F-02)。段階8は U-002・U-003 の手順書。

`create_impact_project`は、この設計で段階1〜8 をすべて承認済み(写しあり)にしたプロジェクトを
DB に作る(印の記録のテスト用。承認の検証を通さず、行を直接作る)。

簡易ドキュメントモードの設計(`simple_impact_documents`・`simple_impact_stage8`)は、同じ要件・
外部設計書に、内部設計書(テーブル reservations・users、層ごとのモジュール一覧、DF-1 = POST・
DF-2 = GET)と実装計画書(U-001 基盤、U-002 登録 = DF-1・FR-001、U-003 一覧 = DF-2・FR-002、
U-003 は U-002 に依存し、サービスの層を持たない)を足し、段階8は3単位すべての手順書を持つ。
`create_simple_impact_project`は、それを段階8承認済みにしたプロジェクトを作る。
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.design_stage import DesignStage
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository

ROUTE = "app/api/routes/reservations.py"
SERVICE = "app/services/reservation.py"
REPOSITORY = "app/repositories/reservation.py"


def _function(fid: str, name: str, trigger: str, req: str) -> dict:
    return {
        "id": fid,
        "name": name,
        "kind": "API",
        "trigger": trigger,
        "screens": ["SCR-001"],
        "group_initial": "reservations",
        "group": "reservations",
        "summary": name,
        "req_ids": [req],
    }


def _module(path: str, depends_on: list[str]) -> dict:
    return {
        "path": path,
        "layer": "api",
        "responsibility": path,
        "depends_on": depends_on,
        "functions": ["F-01", "F-02"],
        "all_functions": False,
    }


def _step(caller: str, callee: str, call: str, db: str = "—") -> dict:
    return {"caller": caller, "callee": callee, "call": call, "action": call, "db": db}


def _logic(module: str, function: str) -> dict:
    return {"module": module, "function": function, "pseudo": [{"text": "処理する"}]}


def _task(key: str, kind: str, title: str, function_ids: list[str]) -> dict:
    modules = [ROUTE, SERVICE, REPOSITORY] if function_ids else []
    return {
        "key": key,
        "kind": kind,
        "title": title,
        "function_ids": function_ids,
        "modules": modules,
    }


def _unit(key: str, unit_id: str, title: str) -> dict:
    files = [{"path": path, "kind": "module"} for path in (ROUTE, SERVICE, REPOSITORY)]
    return {"unit_id": unit_id, "unit_key": key, "title": title, "files": files}


def impact_stages() -> dict[int, dict]:
    """段階1〜8 の model(保存された形の dict)。"""
    return {
        1: {
            "groups": ["reservations"],
            "functions": [
                _function("F-01", "予約を登録する", "POST /api/v1/reservations", "FR-001"),
                _function("F-02", "予約の一覧を見る", "GET /api/v1/reservations", "FR-002"),
            ],
            "next_number": 3,
        },
        2: {"dfd_groups": [], "summaries": [{"function_id": "F-01"}, {"function_id": "F-02"}]},
        3: {
            "cells": [
                {"function_id": "F-01", "table": "reservations", "ops": "C"},
                {"function_id": "F-02", "table": "reservations", "ops": "R"},
            ]
        },
        4: {
            "modules": [
                _module(ROUTE, [SERVICE]),
                _module(SERVICE, [REPOSITORY]),
                _module(REPOSITORY, []),
            ]
        },
        5: {
            "procedures": [
                {
                    "function_id": "F-01",
                    "steps": [
                        _step("利用者", ROUTE, "create_reservation"),
                        _step(ROUTE, SERVICE, "register"),
                        _step(SERVICE, REPOSITORY, "insert", "reservations C"),
                    ],
                },
                {
                    "function_id": "F-02",
                    "steps": [
                        _step("利用者", ROUTE, "list_reservations"),
                        _step(ROUTE, SERVICE, "list"),
                        _step(SERVICE, REPOSITORY, "select", "Reservations R, 本文 —"),
                    ],
                },
            ]
        },
        6: {"logics": [_logic(SERVICE, "register"), _logic(SERVICE, "list")]},
        7: {
            "crosscutting": [
                {"topic": "認証", "policy": "JWT", "modules": [ROUTE], "req_ids": ["NFR-001"]},
                {"topic": "トランザクション", "policy": "サービスで commit", "modules": [SERVICE]},
            ],
            "milestones": [
                {
                    "name": "予約",
                    "tasks": [
                        _task("U-001", "base", "開発環境を用意する", []),
                        _task("U-002", "feature", "予約を登録する", ["F-01"]),
                        _task("U-003", "feature", "予約の一覧を見る", ["F-02"]),
                    ],
                }
            ],
            "next_unit_seq": 4,
        },
        8: {
            "units": [
                _unit("U-002", "M-01-T02", "予約を登録する"),
                _unit("U-003", "M-01-T03", "予約の一覧を見る"),
            ]
        },
    }


def impact_documents() -> dict[str, dict]:
    """要件定義書・外部設計書の model(保存された形の dict)。"""

    def requirement(rid: str, kind: str, text: str) -> dict:
        return {"id": rid, "kind": kind, "text": text, "source": "user"}

    return {
        "requirements": {
            "purpose": "予約を受け付ける",
            "rows": [
                requirement("FR-001", "FR", "予約を登録できる"),
                requirement("FR-002", "FR", "予約の一覧を見られる"),
                requirement("NFR-001", "NFR", "本人の予約だけを見られる"),
            ],
        },
        "external_design": {
            "screens": [{"id": "SCR-001", "name": "予約画面", "req_ids": ["FR-001", "FR-002"]}],
            "apis": [
                {
                    "method": "POST",
                    "path": "/api/v1/reservations",
                    "screens": ["SCR-001"],
                    "req_ids": ["FR-001"],
                },
                {
                    "method": "GET",
                    "path": "/api/v1/reservations",
                    "screens": ["SCR-001"],
                    "req_ids": ["FR-002"],
                },
            ],
            "known_req_ids": ["FR-001", "FR-002", "NFR-001"],
        },
    }


async def create_impact_project(session: AsyncSession) -> Project:
    """詳細設計モードのプロジェクトに、文書(1版ずつ)と段階1〜8(承認済み・写しあり)を作る。
    入力の版(`input_fingerprint`)は記録しない(段階は「古い」にならず、承認済みのまま)。"""
    user = User(email=f"impact-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="detailed", status="completed")
    session.add(project)
    await session.flush()
    documents = GeneratedDocumentRepository(session)
    for doc_type, model in impact_documents().items():
        await documents.create_version(
            project_id=project.id, doc_type=doc_type, content=f"# {doc_type}", model=model
        )
    for stage, model in impact_stages().items():
        session.add(
            DesignStage(
                project_id=project.id,
                stage=stage,
                status="approved",
                model=model,
                version=1,
                approved_version=1,
                approved_model=model,
            )
        )
    await session.commit()
    return project


# 簡易モードの内部設計書のモジュール一覧の行(層ごとにまとめた行)
SIMPLE_ROUTES = "app/api/routes/*.py"
SIMPLE_SERVICES = "app/services/*.py"
SIMPLE_REPOSITORIES = "app/repositories/*.py"


def simple_impact_documents() -> dict[str, dict]:
    """簡易モードの4文書の model(保存された形の dict)。"""

    def module(path: str, layer: str) -> dict:
        return {"path": path, "layer": layer, "responsibility": layer}

    def task(key: str, kind: str, title: str, dfs: list[str], reqs: list[str], **extra) -> dict:
        return {
            "key": key,
            "kind": kind,
            "title": title,
            "function_ids": dfs,
            "req_ids": reqs,
            **extra,
        }

    documents = impact_documents()
    documents["internal_design"] = {
        "tables": [
            {"name": "reservations", "columns": [{"name": "id", "type": "UUID"}]},
            {"name": "users", "columns": [{"name": "id", "type": "UUID"}]},
        ],
        "modules": [
            module(SIMPLE_ROUTES, "api"),
            module(SIMPLE_SERVICES, "service"),
            module(SIMPLE_REPOSITORIES, "repository"),
        ],
        "dataflows": [
            {
                "id": "DF-1",
                "trigger": "POST /api/v1/reservations",
                "flows": [
                    {"source": "利用者", "data": "予約", "target": "`reservations` テーブル"},
                    {"source": "`users` テーブル", "data": "利用者", "target": "サービス"},
                ],
            },
            {
                "id": "DF-2",
                "trigger": "GET /api/v1/reservations",
                "flows": [
                    {"source": "`reservations` テーブル", "data": "予約", "target": "利用者"}
                ],
            },
        ],
        "next_dataflow_seq": 3,
    }
    documents["implementation_plan"] = {
        "milestones": [
            {"name": "基盤", "tasks": [task("U-001", "base", "土台を作る", [], [])]},
            {
                "name": "予約",
                "tasks": [
                    task(
                        "U-002",
                        "feature",
                        "予約を登録する",
                        ["DF-1"],
                        ["FR-001"],
                        modules=[ROUTE, SERVICE, REPOSITORY],
                        depends_on=["M-01-T01"],
                    ),
                    task(
                        "U-003",
                        "feature",
                        "予約の一覧を見る",
                        ["DF-2"],
                        ["FR-002"],
                        modules=[ROUTE, REPOSITORY],
                        depends_on=["M-02-T01"],
                    ),
                ],
            },
        ],
        "next_unit_seq": 4,
    }
    return documents


def simple_impact_stage8() -> dict:
    """簡易モードの段階8の model(3単位すべての手順書)。"""
    return {
        "units": [
            {"unit_id": "M-01-T01", "unit_key": "U-001", "title": "土台を作る"},
            {"unit_id": "M-02-T01", "unit_key": "U-002", "title": "予約を登録する"},
            {"unit_id": "M-02-T02", "unit_key": "U-003", "title": "予約の一覧を見る"},
        ]
    }


async def create_simple_impact_project(
    session: AsyncSession, *, approved: bool = True
) -> Project:
    """簡易モードのプロジェクトに、4文書(1版ずつ)と段階8(`approved`なら承認済み)を作る。"""
    user = User(email=f"impact-{uuid.uuid4()}@example.com", hashed_password="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, title="p", mode="simple", status="completed")
    session.add(project)
    await session.flush()
    documents = GeneratedDocumentRepository(session)
    for doc_type, model in simple_impact_documents().items():
        await documents.create_version(
            project_id=project.id, doc_type=doc_type, content=f"# {doc_type}", model=model
        )
    model = simple_impact_stage8()
    session.add(
        DesignStage(
            project_id=project.id,
            stage=8,
            status="approved" if approved else "reviewing",
            model=model,
            version=1,
            approved_version=1 if approved else None,
            approved_model=model if approved else None,
        )
    )
    await session.commit()
    return project
