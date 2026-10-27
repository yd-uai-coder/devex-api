"""資料の違いの API を実際の PostgreSQL で確かめる(JSONB の理由と鮮度・やり直し・CASCADE・
誤りの応答)。

SUT: POST・GET /api/v1/projects/{id}/materials/{mid}/differences、PATCH .../differences/{did}
     (ルート → MaterialDifferenceService → MaterialDifferenceRepository・BaselineService)と、
     資料の削除での違い・取り出しの回の CASCADE
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ: 取り出しの LLM の解決(LlmResolver.resolve)を、資料の印から違いを返す偽の LLM
(E2eFakeLLM)に差し替える(stub_llm_resolver)。利用者・プロジェクト・文書はセッションで直接作り、
アクセストークンも直接発行する
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from tests.fixtures.change_impact import impact_documents
from tests.fixtures.llm_resolver import stub_llm_resolver

from app.ai.llm.fake import E2eFakeLLM
from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.material_difference import MaterialDifference, MaterialExtraction
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository

pytestmark = pytest.mark.integration

_BASELINE = {"agreed_at": "2026-10-19T10:00:00+09:00", "reason": "要件と外部設計の合意"}
_BODY = (
    "# 第4回 定例\n"
    "- 予約の登録の後に、確認メールを送ってほしい。[違い:change:FR-001]\n"
    "- 予約の画面に、空き状況の表を足してほしい。[違い:add:screen:SCR-001]\n"
    "- 取消の期限は次回までに決める。[違い:question:-]\n"
    "- 電話の受付もやめる。[違い:delete:req:FR-009]\n"
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


async def _rows() -> tuple[int, int]:
    async with AsyncSessionLocal() as session:
        differences = await session.scalar(select(func.count()).select_from(MaterialDifference))
        extractions = await session.scalar(select(func.count()).select_from(MaterialExtraction))
        return differences or 0, extractions or 0


async def test_extract_rerun_dismiss_and_cascade(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = stub_llm_resolver(monkeypatch, E2eFakeLLM())
    headers, user_id = await _login("material-diff-owner@example.com")
    project_id = await _project(user_id)
    materials = f"/api/v1/projects/{project_id}/materials"
    created = await client.post(
        materials,
        json={"kind": "minutes", "title": "定例", "material_date": "2026-10-27", "body": _BODY},
        headers=headers,
    )
    base = f"{materials}/{created.json()['id']}/differences"

    missing = await client.post(base, headers=headers)
    assert (missing.status_code, missing.json()["code"]) == (
        409,
        "MATERIAL_BASELINE_MISSING",
    )
    empty = (await client.get(base, headers=headers)).json()
    assert (empty["extraction"], empty["latest_baseline_number"], empty["differences"]) == (
        None,
        None,
        [],
    )

    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    extracted = await client.post(base, headers=headers)
    assert extracted.status_code == 200
    body = extracted.json()
    rows = [(d["kind"], d["target_key"], d["origin_key"], d["status"]) for d in body["differences"]]
    assert rows == [
        ("change", "req:FR-001", "", "unconfirmed"),
        ("add", "", "screen:SCR-001", "unconfirmed"),
        ("question", "", "", "unconfirmed"),
        ("delete", "req:FR-009", "", "rejected"),
    ]
    assert body["differences"][3]["reasons"] == ["対象 req:FR-009 が束に無い"]
    extraction = body["extraction"]
    assert (extraction["baseline_label"], extraction["freshness"]["fresh"]) == ("B-1", True)
    assert extraction["generated_model"] == "gemini-3.5-flash-lite"
    assert body["latest_baseline_number"] == 1
    assert stub.calls[-1][0] == user_id

    question = body["differences"][2]["id"]
    dismiss = {"action": "dismiss"}
    no_reason = await client.patch(f"{base}/{question}", json=dismiss, headers=headers)
    assert no_reason.status_code == 422
    closed = await client.patch(
        f"{base}/{question}", json={**dismiss, "reason": "次回の定例で確かめる"}, headers=headers
    )
    assert (closed.json()["status"], closed.json()["dismiss_reason"]) == (
        "dismissed",
        "次回の定例で確かめる",
    )
    rejected = body["differences"][3]["id"]
    restore = {"action": "restore"}
    refused = await client.patch(f"{base}/{rejected}", json=restore, headers=headers)
    assert (refused.status_code, refused.json()["code"]) == (
        409,
        "MATERIAL_DIFFERENCE_STATE",
    )
    unknown = await client.patch(f"{base}/{uuid.uuid4()}", json=restore, headers=headers)
    assert unknown.status_code == 404

    # やり直し: 捨てた確かめる点は残り、同じ違いは足し直さない。未確認と弾いたは置き換わる
    rerun = (await client.post(base, headers=headers)).json()
    assert [(d["kind"], d["status"]) for d in rerun["differences"]] == [
        ("question", "dismissed"),
        ("change", "unconfirmed"),
        ("add", "unconfirmed"),
        ("delete", "rejected"),
    ]
    assert await _rows() == (4, 1)

    other_headers, other_id = await _login("material-diff-other@example.com")
    other_project = await _project(other_id)
    hidden = await client.get(
        f"/api/v1/projects/{other_project}/materials/{created.json()['id']}/differences",
        headers=other_headers,
    )
    assert hidden.status_code == 404

    deleted = await client.delete(f"{materials}/{created.json()['id']}", headers=headers)
    assert deleted.status_code == 204
    assert await _rows() == (0, 0)
