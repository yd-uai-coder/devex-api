"""資料の違いから CR の下書きを作る API を実際の PostgreSQL で確かめる(違いと CR の結び・誤りの
応答・資料を消しても CR が残る・基準の束の鮮度と規模の状態の応答)。

SUT: POST /api/v1/projects/{id}/materials/{mid}/change-requests(ルート →
     MaterialDifferenceService.create_change_request → ChangeRequestService.add_draft・
     MaterialDifferenceRepository)と、違いの一覧の候補・CR の番号、マイグレーションで足した
     material_differences.change_request_id の外部キー。CR の詳細・説明書の基準の束の鮮度と
     規模の状態(GET .../change-requests/{n}/explanation)
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ: 取り出しの LLM の解決(LlmResolver.resolve)を、資料の印から違いを返す偽の LLM
(E2eFakeLLM)に差し替える(stub_llm_resolver)。利用者・プロジェクト・文書はセッションで直接作り、
アクセストークンも直接発行する
"""

import uuid

import pytest
from httpx import AsyncClient
from tests.fixtures.change_impact import impact_documents
from tests.fixtures.llm_resolver import stub_llm_resolver

from app.ai.llm.fake import E2eFakeLLM
from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository

pytestmark = pytest.mark.integration

_BASELINE = {"agreed_at": "2026-10-19T10:00:00+09:00", "reason": "要件と外部設計の合意"}
_BODY = (
    "# 第5回 定例\n"
    "- 予約の登録の後に、確認メールを送ってほしい。[違い:change:req:FR-001]\n"
    "- 予約の画面に、空き状況の表を足してほしい。[違い:add:screen:SCR-001]\n"
    "- 予約の画面は、日付の新しい順に並べる。[違い:change:screen:SCR-001]\n"
    "- 取消の期限は次回までに決める。[違い:question:-]\n"
)


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
        for doc_type, model in impact_documents().items():
            await GeneratedDocumentRepository(session).create_version(
                project_id=project.id, doc_type=doc_type, content=f"# {doc_type}", model=model
            )
        await session.commit()
        return project.id


async def test_differences_to_change_request_draft(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_llm_resolver(monkeypatch, E2eFakeLLM())
    headers, user_id = await _login("material-cr-owner@example.com")
    project_id = await _project(user_id)
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    materials = f"/api/v1/projects/{project_id}/materials"
    created = await client.post(
        materials,
        json={"kind": "email", "title": "予約の画面", "material_date": "2026-10-28", "body": _BODY},
        headers=headers,
    )
    material = f"{materials}/{created.json()['id']}"
    extracted = (await client.post(f"{material}/differences", headers=headers)).json()
    change, add, screen, question = (d["id"] for d in extracted["differences"])
    assert [d["requirement_candidates"] for d in extracted["differences"]] == [
        ["FR-001"],
        [],
        ["FR-001", "FR-002"],
        [],
    ]

    invalid = await client.post(
        f"{material}/change-requests",
        json={"difference_ids": [screen], "requirement_ids": ["NFR-001"]},
        headers=headers,
    )
    assert (invalid.status_code, invalid.json()["code"]) == (422, "MATERIAL_DRAFT_INVALID")
    refused = await client.post(
        f"{material}/change-requests", json={"difference_ids": [question]}, headers=headers
    )
    assert (refused.status_code, refused.json()["code"]) == (409, "MATERIAL_DIFFERENCE_STATE")

    made = await client.post(
        f"{material}/change-requests",
        json={"difference_ids": [change, add, screen], "requirement_ids": ["FR-002"]},
        headers=headers,
    )
    assert made.status_code == 201
    cr = made.json()
    assert (cr["label"], cr["status"], cr["baseline_number"]) == ("CR-1", "draft", 1)
    assert cr["title"] == "「予約の画面」の変更(3件)"
    assert (cr["background_source"], cr["background_date"]) == ("email", "2026-10-28")
    assert [(t["requirement_id"], t["kind"]) for t in cr["targets"]] == [
        ("FR-001", "content"),
        ("FR-002", "content"),
    ]
    assert cr["adds_requirement"] is True
    assert [o["key"] for o in cr["origins"]] == ["screen:SCR-001"]
    # 基準の束は最新で、束の後に反映した CR は無い(古ければ知らせる)
    assert (cr["baseline_freshness"]["label"], cr["baseline_freshness"]["fresh"]) == ("B-1", True)
    explanation = (
        await client.get(
            f"/api/v1/projects/{project_id}/change-requests/1/explanation", headers=headers
        )
    ).json()
    assert "合意済みの版 B-1 の設計から数えました。" in explanation["markdown"]
    assert explanation["baseline_freshness"]["fresh"] is True
    assert {c["state"] for c in explanation["scale"]} <= {"counted", "uncountable"}

    listed = (await client.get(f"{material}/differences", headers=headers)).json()
    assert [(d["status"], d["change_request_number"]) for d in listed["differences"]] == [
        ("converted", 1),
        ("converted", 1),
        ("converted", 1),
        ("unconfirmed", None),
    ]
    again = await client.post(
        f"{material}/change-requests", json={"difference_ids": [change]}, headers=headers
    )
    assert (again.status_code, again.json()["code"]) == (409, "MATERIAL_DIFFERENCE_STATE")

    other_headers, other_id = await _login("material-cr-other@example.com")
    other_project = await _project(other_id)
    hidden = await client.post(
        f"/api/v1/projects/{other_project}/materials/{created.json()['id']}/change-requests",
        json={"difference_ids": [question]},
        headers=other_headers,
    )
    assert hidden.status_code == 404

    # 資料を消しても、作った CR は残る(背景の出どころは CR の文)
    assert (await client.delete(material, headers=headers)).status_code == 204
    kept = await client.get(f"/api/v1/projects/{project_id}/change-requests/1", headers=headers)
    assert (kept.status_code, kept.json()["background_source"]) == (200, "email")
