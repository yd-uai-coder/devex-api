"""合意済みの版(束)の API を実際の PostgreSQL で確かめる(JSONB の写し・一意制約・CASCADE)。

SUT: POST・GET /api/v1/projects/{id}/baselines、GET .../baselines/{number}(ルート →
     BaselineService → BaselineRepository)と、プロジェクトの削除での束の CASCADE
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
from app.models.baseline import Baseline, BaselineItem
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository

pytestmark = pytest.mark.integration

_BODY = {
    "counterpart": "山田様",
    "agreed_at": "2026-10-19T10:00:00+09:00",
    "memo": "定例で確認",
    "reason": "要件と外部設計の合意",
}


async def _login(email: str) -> tuple[dict[str, str], uuid.UUID]:
    async with AsyncSessionLocal() as session:
        user = User(email=email, hashed_password="x")
        session.add(user)
        await session.commit()
        user_id = user.id
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}, user_id


async def _project(
    user_id: uuid.UUID, *, mode: str = "detailed", documents: tuple[str, ...] = ()
) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        project = Project(user_id=user_id, title="備品予約", mode=mode, status="completed")
        session.add(project)
        await session.flush()
        for doc_type in documents:
            await GeneratedDocumentRepository(session).create_version(
                project_id=project.id,
                doc_type=doc_type,
                content=f"# {doc_type}",
                model={"rows": [{"id": "FR-001"}]},
                generated_model="m-1",
            )
        await session.commit()
        return project.id


async def _baseline_rows(project_id: uuid.UUID) -> tuple[int, int]:
    async with AsyncSessionLocal() as session:
        baselines = await session.scalar(
            select(func.count()).select_from(Baseline).where(Baseline.project_id == project_id)
        )
        items = await session.scalar(
            select(func.count())
            .select_from(BaselineItem)
            .join(Baseline)
            .where(Baseline.project_id == project_id)
        )
        return baselines or 0, items or 0


async def test_create_list_get_and_cascade(client: AsyncClient) -> None:
    headers, user_id = await _login("baseline-owner@example.com")
    project_id = await _project(user_id, documents=("requirements", "external_design"))
    base = f"/api/v1/projects/{project_id}/baselines"

    created = await client.post(base, json=_BODY, headers=headers)
    second = await client.post(base, json={**_BODY, "counterpart": None}, headers=headers)

    assert created.status_code == 201
    body = created.json()
    assert (body["label"], body["counterpart"], body["recorded_by"]) == (
        "B-1",
        "山田様",
        str(user_id),
    )
    requirements = body["items"][0]
    assert requirements["key"] == "requirements"
    assert requirements["model"] == {"rows": [{"id": "FR-001"}]}
    assert requirements["generated_model"] == "m-1"
    assert second.json()["label"] == "B-2"
    assert second.json()["counterpart"] is None

    listed = await client.get(base, headers=headers)
    assert [b["label"] for b in listed.json()] == ["B-2", "B-1"]
    assert "content" not in listed.json()[0]["items"][0]
    fetched = await client.get(f"{base}/1", headers=headers)
    assert fetched.json()["items"][0]["content"] == "# requirements"
    assert (await client.get(f"{base}/9", headers=headers)).status_code == 404

    assert await _baseline_rows(project_id) == (2, 22)  # 文書3 + 段階8 が2束
    deleted = await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert deleted.status_code == 204
    assert await _baseline_rows(project_id) == (0, 0)


async def test_conditions_and_ownership(client: AsyncClient) -> None:
    headers, user_id = await _login("baseline-rules@example.com")
    other_headers, _ = await _login("baseline-other@example.com")
    simple_id = await _project(user_id, mode="simple", documents=("requirements",))
    no_docs_id = await _project(user_id, documents=("requirements",))

    simple = await client.post(
        f"/api/v1/projects/{simple_id}/baselines", json=_BODY, headers=headers
    )
    missing = await client.post(
        f"/api/v1/projects/{no_docs_id}/baselines", json=_BODY, headers=headers
    )
    other = await client.get(f"/api/v1/projects/{no_docs_id}/baselines", headers=other_headers)
    invalid = await client.post(
        f"/api/v1/projects/{no_docs_id}/baselines", json={**_BODY, "reason": ""}, headers=headers
    )

    assert (simple.status_code, simple.json()["code"]) == (409, "DESIGN_STAGES_NOT_AVAILABLE")
    assert (missing.status_code, missing.json()["code"]) == (409, "BASELINE_DOCUMENTS_MISSING")
    assert other.status_code == 404
    assert invalid.status_code == 422
