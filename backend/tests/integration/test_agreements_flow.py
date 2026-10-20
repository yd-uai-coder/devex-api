"""今の合意の一覧の API を実際の PostgreSQL で確かめる(JSONB の写し・束と「採用にする」からの記録・
CASCADE)。

SUT: GET /api/v1/projects/{id}/agreements(ルート → AgreementService → AgreementRepository)と、
     束の作成(POST .../baselines)・「採用にする」(POST .../documents/requirements/rows/{id}/adopt)
     での記録、プロジェクトの削除での記録の CASCADE
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ不要 ── LLM を呼ばない。利用者・プロジェクト・文書はセッションで直接作り、アクセストークンも
直接発行する(API で登録すると、スイート全体で登録・ログインの IP ごとの上限に当たるため)
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from tests.fixtures.change_impact import impact_documents

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.agreement import AgreementRecord
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository

pytestmark = pytest.mark.integration

_BODY = {"agreed_at": "2026-10-20T10:00:00+09:00", "reason": "要件と外部設計の合意"}


async def _login(email: str) -> tuple[dict[str, str], uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(email=email, hashed_password="x")
        session.add(user)
        await session.commit()
        user_id = user.id
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}, user_id


async def _project(user_id: uuid.UUID, *, mode: str = "detailed") -> uuid.UUID:
    """要件定義書(FR-002 は推測)と外部設計書が1版ずつあるプロジェクト。"""
    documents = impact_documents()
    rows = documents["requirements"]["rows"]
    documents["requirements"]["rows"] = [rows[0], {**rows[1], "source": "assumed"}, rows[2]]
    async with AsyncSessionLocal() as session:
        project = Project(user_id=user_id, title="備品予約", mode=mode, status="completed")
        session.add(project)
        await session.flush()
        for doc_type, model in documents.items():
            await GeneratedDocumentRepository(session).create_version(
                project_id=project.id, doc_type=doc_type, content=f"# {doc_type}", model=model
            )
        await session.commit()
        return project.id


async def _records(project_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AgreementRecord)
            .where(AgreementRecord.project_id == project_id)
        )
        return count or 0


async def test_baseline_and_adopt_make_current_agreements(client: AsyncClient) -> None:
    headers, user_id = await _login("agreement-owner@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}"

    assert (await client.post(f"{base}/baselines", json=_BODY, headers=headers)).status_code == 201
    adopted = await client.post(
        f"{base}/documents/requirements/rows/FR-002/adopt", headers=headers
    )
    assert adopted.status_code == 200
    listed = await client.get(f"{base}/agreements", headers=headers)

    assert listed.status_code == 200
    body = listed.json()
    assert [(a["key"], a["origin_label"]) for a in body] == [
        ("req:FR-001", "B-1"),
        ("req:NFR-001", "B-1"),
        ("req:FR-002", "採用にする"),
        ("screen:SCR-001", "B-1"),
        ("api:POST /api/v1/reservations", "B-1"),
        ("api:GET /api/v1/reservations", "B-1"),
    ]
    fr2 = body[2]
    assert (fr2["kind"], fr2["origin"]) == ("requirement", "adopt")
    assert fr2["recorded_by"] == str(user_id)
    assert fr2["content"]["source"] == "adopted"
    assert fr2["baseline_number"] is None
    assert body[0]["baseline_number"] == 1
    assert body[0]["content"]["text"] == "予約を登録できる"

    assert await _records(project_id) == 7  # 束の6要素 + 採用1
    deleted = await client.delete(base, headers=headers)
    assert deleted.status_code == 204
    assert await _records(project_id) == 0


async def test_mode_and_ownership(client: AsyncClient) -> None:
    headers, user_id = await _login("agreement-rules@example.com")
    other_headers, _ = await _login("agreement-other@example.com")
    simple_id = await _project(user_id, mode="simple")
    detailed_id = await _project(user_id)

    simple = await client.get(f"/api/v1/projects/{simple_id}/agreements", headers=headers)
    empty = await client.get(f"/api/v1/projects/{detailed_id}/agreements", headers=headers)
    other = await client.get(f"/api/v1/projects/{detailed_id}/agreements", headers=other_headers)
    anonymous = await client.get(f"/api/v1/projects/{detailed_id}/agreements")
    # 簡易モードでも「採用にする」はでき、合意は記録しない
    adopted = await client.post(
        f"/api/v1/projects/{simple_id}/documents/requirements/rows/FR-002/adopt", headers=headers
    )

    assert (simple.status_code, simple.json()["code"]) == (409, "DESIGN_STAGES_NOT_AVAILABLE")
    assert (empty.status_code, empty.json()) == (200, [])
    assert other.status_code == 404
    assert anonymous.status_code == 401
    assert adopted.status_code == 200
    assert await _records(simple_id) == 0
