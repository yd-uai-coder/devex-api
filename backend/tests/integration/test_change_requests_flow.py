"""変更要求(CR)の API を実際の PostgreSQL で確かめる(JSONB の対象・時系列・自己参照・CASCADE)。

SUT: POST・GET /api/v1/projects/{id}/change-requests、GET・PATCH .../{number}、
     POST .../{number}/explain・decision・withdraw・copy(ルート → ChangeRequestService →
     ChangeRequestRepository)と、プロジェクトの削除での CR と時系列の CASCADE
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ不要 ── LLM を呼ばない。利用者・プロジェクト・文書はセッションで直接作り、アクセストークンも
直接発行する(API で登録すると、スイート全体で登録・ログインの IP ごとの上限に当たるため)
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.change_request import ChangeRequest, ChangeRequestEvent
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository

pytestmark = pytest.mark.integration

_BASELINE = {"agreed_at": "2026-10-19T10:00:00+09:00", "reason": "要件と外部設計の合意"}
_CR = {
    "title": "参考のファイルを5つまで",
    "content": "3つから5つへ増やす",
    "background_source": "meeting",
    "background_date": "2026-10-18",
    "background_memo": "定例で依頼",
    "target_requirement_ids": ["FR-001"],
}
_DECISION = {
    "decision": "rejected",
    "decided_at": "2026-10-20T15:00:00+09:00",
    "counterpart": "山田様(情報システム部)",
    "reason": "今回の範囲の外",
}


async def _login(email: str) -> tuple[dict[str, str], uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(email=email, hashed_password="x")
        session.add(user)
        await session.commit()
        user_id = user.id
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}, user_id


async def _project(user_id: uuid.UUID) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        project = Project(user_id=user_id, title="備品予約", mode="detailed", status="completed")
        session.add(project)
        await session.flush()
        for doc_type in ("requirements", "external_design"):
            await GeneratedDocumentRepository(session).create_version(
                project_id=project.id,
                doc_type=doc_type,
                content=f"# {doc_type}",
                model={"rows": [{"id": "FR-001"}, {"id": "FR-002"}]},
            )
        await session.commit()
        return project.id


async def _rows(project_id: uuid.UUID) -> tuple[int, int]:
    async with AsyncSessionLocal() as session:
        requests = await session.scalar(
            select(func.count())
            .select_from(ChangeRequest)
            .where(ChangeRequest.project_id == project_id)
        )
        events = await session.scalar(
            select(func.count())
            .select_from(ChangeRequestEvent)
            .join(ChangeRequest)
            .where(ChangeRequest.project_id == project_id)
        )
        return requests or 0, events or 0


async def test_reject_copy_agree_and_cascade(client: AsyncClient) -> None:
    headers, user_id = await _login("cr-owner@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}/change-requests"

    missing = await client.post(base, json=_CR, headers=headers)
    assert (missing.status_code, missing.json()["code"]) == (409, "CR_BASELINE_MISSING")
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)

    created = await client.post(base, json=_CR, headers=headers)
    assert created.status_code == 201
    assert (created.json()["label"], created.json()["baseline_label"]) == ("CR-1", "B-1")
    assert (await client.post(f"{base}/1/explain", headers=headers)).status_code == 200
    rejected = await client.post(f"{base}/1/decision", json=_DECISION, headers=headers)
    assert rejected.json()["decision"]["counterpart"] == "山田様(情報システム部)"

    copied = await client.post(f"{base}/1/copy", headers=headers)
    assert copied.status_code == 201
    assert (copied.json()["label"], copied.json()["derived_from_number"]) == ("CR-2", 1)
    patched = await client.patch(
        f"{base}/2",
        json={"target_requirement_ids": ["FR-001", "FR-002"], "background_date": None},
        headers=headers,
    )
    assert patched.json()["target_requirement_ids"] == ["FR-001", "FR-002"]
    assert patched.json()["background_date"] is None
    await client.post(f"{base}/2/explain", headers=headers)
    agreed = await client.post(
        f"{base}/2/decision", json={**_DECISION, "decision": "agreed"}, headers=headers
    )
    assert agreed.json()["status"] == "agreed"
    assert [e["to_status"] for e in agreed.json()["events"]] == ["draft", "explained", "agreed"]

    source = (await client.get(f"{base}/1", headers=headers)).json()
    assert (source["status"], source["derived_numbers"]) == ("rejected", [2])
    assert [e["action"] for e in source["events"]] == ["create", "explain", "reject", "copied"]
    listed = await client.get(base, headers=headers)
    assert [c["label"] for c in listed.json()] == ["CR-2", "CR-1"]

    assert await _rows(project_id) == (2, 7)
    assert (
        await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    ).status_code == 204
    assert await _rows(project_id) == (0, 0)


async def test_errors_overlap_and_ownership(client: AsyncClient) -> None:
    headers, user_id = await _login("cr-rules@example.com")
    other_headers, _ = await _login("cr-other@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}/change-requests"
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    await client.post(base, json=_CR, headers=headers)

    unknown = await client.post(
        base, json={**_CR, "target_requirement_ids": ["FR-404"]}, headers=headers
    )
    no_target = await client.post(
        base, json={**_CR, "target_requirement_ids": []}, headers=headers
    )
    invalid = await client.post(base, json={**_CR, "background_source": "phone"}, headers=headers)
    early = await client.post(f"{base}/1/decision", json=_DECISION, headers=headers)
    not_found = await client.get(f"{base}/9", headers=headers)
    other = await client.get(base, headers=other_headers)
    second = await client.post(
        base, json={**_CR, "target_requirement_ids": ["FR-002", "FR-001"]}, headers=headers
    )

    assert (unknown.status_code, unknown.json()["code"]) == (422, "CR_TARGET_UNKNOWN")
    assert no_target.status_code == 422
    assert invalid.status_code == 422
    assert (early.status_code, early.json()["code"]) == (409, "CR_INVALID_TRANSITION")
    assert not_found.status_code == 404
    assert other.status_code == 404
    assert second.json()["overlapping"] == [
        {"number": 1, "label": "CR-1", "requirement_ids": ["FR-001"]}
    ]
