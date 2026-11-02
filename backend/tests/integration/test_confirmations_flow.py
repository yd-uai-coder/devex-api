"""確認事項の一覧の API を実際の PostgreSQL で確かめる(一覧の組み立て・欄の保存・確認済みでの
採用と合意・CASCADE)。

SUT: GET /api/v1/projects/{id}/confirmations と PATCH .../confirmations/{key}(ルート →
     ConfirmationService → ConfirmationRepository・DocGeneratorService・AgreementService)、
     プロジェクトの削除での行の CASCADE
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
from app.models.confirmation import ConfirmationItemRecord
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository

pytestmark = pytest.mark.integration


async def _login(email: str) -> tuple[dict[str, str], uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(email=email, hashed_password="x")
        session.add(user)
        await session.commit()
        user_id = user.id
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}, user_id


async def _project(user_id: uuid.UUID, *, mode: str = "detailed") -> uuid.UUID:
    """要件定義書(FR-002 は推測で、根拠は例外の観点)が1版あるプロジェクト。観点の表は空
    (すべて未決)。"""
    model = impact_documents()["requirements"]
    rows = model["rows"]
    model["rows"] = [rows[0], {**rows[1], "source": "assumed", "points": ["exceptions"]}, rows[2]]
    async with AsyncSessionLocal() as session:
        project = Project(user_id=user_id, title="備品予約", mode=mode, status="completed")
        session.add(project)
        await session.flush()
        await GeneratedDocumentRepository(session).create_version(
            project_id=project.id, doc_type="requirements", content="# r", model=model
        )
        await session.commit()
        return project.id


async def _rows(project_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ConfirmationItemRecord)
            .where(ConfirmationItemRecord.project_id == project_id)
        )
        return count or 0


async def test_list_update_confirm_and_cascade(client: AsyncClient) -> None:
    headers, user_id = await _login("confirmation-owner@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}"

    listed = await client.get(f"{base}/confirmations", headers=headers)
    assert listed.status_code == 200
    keys = [i["key"] for i in listed.json()["items"]]
    assert keys == ["req:FR-002", "point:exceptions"]

    point = keys[1]
    noted = await client.patch(
        f"{base}/confirmations/{point}",
        json={"opportunity": "email", "memo": "メールで聞く"},
        headers=headers,
    )
    assert noted.status_code == 200
    item = next(i for i in noted.json()["items"] if i["key"] == point)
    assert (item["opportunity_label"], item["memo"]) == ("メール", "メールで聞く")

    confirmed = await client.patch(
        f"{base}/confirmations/req:FR-002", json={"status": "confirmed"}, headers=headers
    )
    assert confirmed.status_code == 200
    body = confirmed.json()
    fr2 = next(i for i in body["items"] if i["key"] == "req:FR-002")
    assert (fr2["present"], fr2["status"]) == (False, "confirmed")
    assert body["adoption"] == {"confirmed": 1, "rejected": 0, "unconfirmed": 0, "rate": 1.0}
    agreements = (await client.get(f"{base}/agreements", headers=headers)).json()
    assert [(a["key"], a["origin"]) for a in agreements] == [("req:FR-002", "adopt")]

    missing = await client.patch(
        f"{base}/confirmations/req:FR-099", json={"status": "confirmed"}, headers=headers
    )
    assert (missing.status_code, missing.json()["code"]) == (404, "CONFIRMATION_ITEM_NOT_FOUND")
    invalid = await client.patch(
        f"{base}/confirmations/{point}", json={"status": None}, headers=headers
    )
    assert invalid.status_code == 422

    assert await _rows(project_id) == 2
    assert (await client.delete(base, headers=headers)).status_code == 204
    assert await _rows(project_id) == 0


async def test_mode_and_ownership(client: AsyncClient) -> None:
    headers, user_id = await _login("confirmation-rules@example.com")
    other_headers, _ = await _login("confirmation-other@example.com")
    simple_id = await _project(user_id, mode="simple")
    detailed_id = await _project(user_id)

    simple = await client.get(f"/api/v1/projects/{simple_id}/confirmations", headers=headers)
    patched = await client.patch(
        f"/api/v1/projects/{simple_id}/confirmations/req:FR-002",
        json={"status": "rejected"},
        headers=headers,
    )
    other = await client.get(
        f"/api/v1/projects/{detailed_id}/confirmations", headers=other_headers
    )
    anonymous = await client.get(f"/api/v1/projects/{detailed_id}/confirmations")

    assert (simple.status_code, simple.json()["code"]) == (409, "DESIGN_STAGES_NOT_AVAILABLE")
    assert patched.status_code == 409
    assert other.status_code == 404
    assert anonymous.status_code == 401
