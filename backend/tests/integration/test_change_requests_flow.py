"""変更要求(CR)の API を実際の PostgreSQL で確かめる(JSONB の対象と予測・時系列・自己参照・
説明書と要約・CASCADE)。

SUT: POST・GET /api/v1/projects/{id}/change-requests、GET・PATCH .../{number}、
     POST .../{number}/explain・decision・withdraw・copy、GET .../{number}/impact、
     GET .../{number}/explanation・explanation/download、POST・PATCH .../explanation/summary、
     POST .../explanation/summary/confirm、CR を渡した POST /api/v1/projects/{id}/generate と
     GET .../{number}/comparison、GET .../{number}/verification・PUT .../document-checks・
     POST .../verify、GET .../review-candidates・POST .../reviews、束の作成の
     change_request_numbers、GET .../{number}/trace、新しい要件の予測の起点
     (GET /api/v1/projects/{id}/baselines/{number}/origins と CR の`origins`)
     (ルート → ChangeRequestService・
     DocGeneratorService・BaselineService → ChangeRequestRepository・
     ChangeRequestSummaryRepository・GeneratedDocumentRepository・ReviewRunRepository)と、
     プロジェクトの削除での CR・時系列・要約・結んだレビューの CASCADE
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ: 要約と文書の生成の LLM だけ、解決(LlmResolver.resolve)を固定の文・構造化出力の見本を
返すフェイクに差し替える(stub_llm_resolver)。ほかは LLM を呼ばない。利用者・プロジェクト・
文書はセッションで直接作り、アクセストークンも直接発行する(API で登録すると、スイート全体で
登録・ログインの IP ごとの上限に当たるため)
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from tests.fixtures.change_impact import impact_documents
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.llm_resolver import stub_llm_resolver
from tests.fixtures.requirements import requirements_output

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.change_request import ChangeRequest, ChangeRequestEvent, ChangeRequestReview
from app.models.change_request_summary import ChangeRequestSummary
from app.models.generated_document import GeneratedDocument
from app.models.project import Project
from app.models.review import ReviewRun
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.review import ReviewRunRepository

pytestmark = pytest.mark.integration

_BASELINE = {"agreed_at": "2026-10-19T10:00:00+09:00", "reason": "要件と外部設計の合意"}
_CR = {
    "title": "参考のファイルを5つまで",
    "content": "3つから5つへ増やす",
    "background_source": "meeting",
    "background_date": "2026-10-18",
    "background_memo": "定例で依頼",
    "targets": [{"requirement_id": "FR-001"}],
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
        for doc_type, model in impact_documents().items():
            await GeneratedDocumentRepository(session).create_version(
                project_id=project.id, doc_type=doc_type, content=f"# {doc_type}", model=model
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
        json={
            "targets": [
                {"requirement_id": "FR-001"},
                {"requirement_id": "FR-002", "kind": "delete"},
            ],
            "background_date": None,
        },
        headers=headers,
    )
    assert [(t["requirement_id"], t["kind_label"]) for t in patched.json()["targets"]] == [
        ("FR-001", "内容の変更"),
        ("FR-002", "削除"),
    ]
    assert patched.json()["background_date"] is None
    await client.post(f"{base}/2/explain", headers=headers)
    agreed = await client.post(
        f"{base}/2/decision", json={**_DECISION, "decision": "agreed"}, headers=headers
    )
    assert agreed.json()["status"] == "agreed"
    # 判断の時点の予測が JSON の列に残り、読み直しても同じ形で返る
    impact = (await client.get(f"{base}/2/impact", headers=headers)).json()
    assert (impact["saved"], impact["baseline_label"]) == (True, "B-1")
    assert [(t["requirement_id"], t["kind"]) for t in impact["targets"]] == [
        ("FR-001", "content"),
        ("FR-002", "delete"),
    ]
    # 段階は承認していないので、予測は文書の側の要素(画面・API)だけ
    assert {i["key"] for i in impact["items"]} == {
        "screen:SCR-001",
        "api:POST /api/v1/reservations",
        "api:GET /api/v1/reservations",
    }
    assert {"stage:1", "stage:8"} <= {e["key"] for e in impact["excluded"]}
    # 下書きの中の直しも時系列に残り、たどる記録に直した項目の名前が出る
    assert [e["to_status"] for e in agreed.json()["events"]] == [
        "draft",
        "draft",
        "explained",
        "agreed",
    ]
    trace = (await client.get(f"{base}/2/trace", headers=headers)).json()
    assert [(e["action"], e["edited_fields"]) for e in trace["entries"][:2]] == [
        ("copy", []),
        ("edit", ["背景の日付", "対象"]),
    ]

    source = (await client.get(f"{base}/1", headers=headers)).json()
    assert (source["status"], source["derived_numbers"]) == ("rejected", [2])
    assert [e["action"] for e in source["events"]] == ["create", "explain", "reject", "copied"]
    listed = await client.get(base, headers=headers)
    assert [c["label"] for c in listed.json()] == ["CR-2", "CR-1"]

    assert await _rows(project_id) == (2, 8)  # CR-2 の下書きの直しの1行を含む
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
        base, json={**_CR, "targets": [{"requirement_id": "FR-404"}]}, headers=headers
    )
    no_target = await client.post(base, json={**_CR, "targets": []}, headers=headers)
    invalid = await client.post(base, json={**_CR, "background_source": "phone"}, headers=headers)
    early = await client.post(f"{base}/1/decision", json=_DECISION, headers=headers)
    not_found = await client.get(f"{base}/9", headers=headers)
    other = await client.get(base, headers=other_headers)
    second = await client.post(
        base,
        json={
            **_CR,
            "targets": [
                {"requirement_id": "FR-002"},
                {"requirement_id": "FR-001", "kind": "delete"},
            ],
        },
        headers=headers,
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


async def test_new_requirement_origins(client: AsyncClient) -> None:
    """起点の候補を束から読み、新しい要件を足す CR に起点を持たせると、JSONB の列に表示名つきで
    残り、予測は起点から求まる(判断の後は保存した予測にも残る)。"""
    headers, user_id = await _login("cr-origins@example.com")
    project_id = await _project(user_id)
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    base = f"/api/v1/projects/{project_id}/change-requests"

    candidates = await client.get(
        f"/api/v1/projects/{project_id}/baselines/1/origins", headers=headers
    )
    assert candidates.status_code == 200
    screen = next(c for c in candidates.json() if c["key"] == "screen:SCR-001")
    assert (screen["kind"], screen["label"]) == ("screen", "SCR-001 予約画面")
    payload = {**_CR, "targets": [], "adds_requirement": True, "origins": ["screen:SCR-001"]}
    unknown = await client.post(
        base, json={**payload, "origins": ["screen:SCR-404"]}, headers=headers
    )
    assert (unknown.status_code, unknown.json()["code"]) == (422, "CR_TARGET_UNKNOWN")

    created = await client.post(base, json=payload, headers=headers)
    assert created.status_code == 201
    assert created.json()["origins"] == [{"key": "screen:SCR-001", "label": "SCR-001 予約画面"}]
    await client.post(f"{base}/1/explain", headers=headers)
    agreed = {**_DECISION, "decision": "agreed"}
    await client.post(f"{base}/1/decision", json=agreed, headers=headers)
    impact = (await client.get(f"{base}/1/impact", headers=headers)).json()
    assert (impact["saved"], impact["unpredicted_new_requirement"]) == (True, False)
    assert impact["origins"] == [{"key": "screen:SCR-001", "label": "SCR-001 予約画面"}]
    item = next(i for i in impact["items"] if i["key"] == "screen:SCR-001")
    assert item["origin_keys"] == ["screen:SCR-001"]


async def _request_ids(project_id: uuid.UUID) -> list[uuid.UUID]:
    async with AsyncSessionLocal() as session:
        rows = await session.scalars(
            select(ChangeRequest.id).where(ChangeRequest.project_id == project_id)
        )
        return list(rows)


async def _summaries(request_ids: list[uuid.UUID]) -> int:
    """CR の要約の行の数(CR が消えた後も数えられるよう、CR の id で数える)。"""
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ChangeRequestSummary)
            .where(ChangeRequestSummary.change_request_id.in_(request_ids))
        )
        return count or 0


async def test_explanation_summary_and_cascade(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_llm_resolver(monkeypatch, FakeLLM(content="FR-001 の参考のファイルを5つまでにします。"))
    headers, user_id = await _login("cr-explain@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}/change-requests"
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    await client.post(
        base,
        json={
            **_CR,
            "targets": [
                {"requirement_id": "FR-001", "proposed_text": "参考のファイルを5つまで登録できる"},
                {"requirement_id": "FR-002", "kind": "delete"},
            ],
        },
        headers=headers,
    )

    preview = (await client.get(f"{base}/1/explanation", headers=headers)).json()
    assert preview["filename"] == "cr-1-explanation.md"
    assert "| FR-001 | 内容の変更 | 予約を登録できる | 参考のファイルを5つまで登録できる |" in (
        preview["markdown"]
    )
    assert "| FR-002 | 削除 | 予約の一覧を見られる | (削除) |" in preview["markdown"]
    assert preview["summary"] is None

    generated = await client.post(f"{base}/1/explanation/summary", headers=headers)
    assert generated.status_code == 200
    assert generated.json()["summary"]["state"] == "unconfirmed"
    assert generated.json()["summary"]["unknown_numbers"] == []
    edited = await client.patch(
        f"{base}/1/explanation/summary", json={"text": " "}, headers=headers
    )
    assert edited.status_code == 422
    confirmed = await client.post(f"{base}/1/explanation/summary/confirm", headers=headers)
    assert confirmed.json()["summary"]["state"] == "confirmed"

    download = await client.get(f"{base}/1/explanation/download", headers=headers)
    assert download.headers["content-type"].startswith("text/markdown")
    assert "cr-1-explanation.md" in download.headers["content-disposition"]
    assert "## 要約\n\nFR-001 の参考のファイルを5つまでにします。" in download.text

    # CR を直すと要約は古くなり、ダウンロードから外れる
    await client.patch(f"{base}/1", json={"content": "3つから6つへ"}, headers=headers)
    stale = (await client.get(f"{base}/1/explanation", headers=headers)).json()
    assert stale["summary"]["state"] == "stale"
    assert "## 要約" not in stale["output"]

    request_ids = await _request_ids(project_id)
    assert await _summaries(request_ids) == 1
    await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert await _summaries(request_ids) == 0


async def test_reflection_records_versions_and_comparison(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_llm_resolver(
        monkeypatch,
        FakeLLM(
            content_sequence=["## 自己診断\n最重要: 特になし"],
            structured_by_schema=upstream_outputs(
                requirements_output(prev_id="FR-001", text="予約を登録し、確認メールを送る")
            ),
        ),
    )
    headers, user_id = await _login("cr-apply@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}/change-requests"
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    await client.post(base, json=_CR, headers=headers)
    generate = f"/api/v1/projects/{project_id}/generate"

    draft = await client.post(
        generate, json={"start_from": "requirements", "cr_number": 1}, headers=headers
    )
    assert (draft.status_code, draft.json()["code"]) == (409, "CR_NOT_REFLECTABLE")
    early = await client.get(f"{base}/1/comparison", headers=headers)
    assert (early.status_code, early.json()["code"]) == (409, "CR_NOT_APPLIED")
    await client.post(f"{base}/1/explain", headers=headers)
    await client.post(
        f"{base}/1/decision", json={**_DECISION, "decision": "agreed"}, headers=headers
    )

    accepted = await client.post(
        generate, json={"start_from": "requirements", "cr_number": 1}, headers=headers
    )
    assert accepted.status_code == 202

    detail = (await client.get(f"{base}/1", headers=headers)).json()
    assert detail["status"] == "applied"
    apply = detail["events"][-1]
    assert apply["action"] == "apply"
    assert [d["doc_type"] for d in apply["refs"]["documents"]] == [
        "requirements",
        "external_design",
    ]
    async with AsyncSessionLocal() as session:
        linked = await session.scalar(
            select(func.count())
            .select_from(GeneratedDocument)
            .join(ChangeRequest, GeneratedDocument.change_request_id == ChangeRequest.id)
            .where(ChangeRequest.project_id == project_id)
        )
    assert linked == 2
    # 段階を承認していないので印は付かず、予測はすべて「予測だけ」
    comparison = (await client.get(f"{base}/1/comparison", headers=headers)).json()
    assert comparison["counts"]["matched"] == 0
    assert comparison["counts"]["predicted_only"] == len(comparison["items"]) > 0
    assert {i["predicted_only_reason"] for i in comparison["items"]} <= {
        "document_side",
        "unmarked_stage",
    }

    await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert await _rows(project_id) == (0, 0)


async def _review_run(project_id: uuid.UUID) -> uuid.UUID:
    """成功したレビューの回(LLM を呼ばずに直接作る)。"""
    async with AsyncSessionLocal() as session:
        run = await ReviewRunRepository(session).create_run(
            project_id=project_id,
            gate="documents",
            targets={},
            trigger="manual",
            model="m",
            status="succeeded",
        )
        await session.commit()
        return run.id


async def _links(project_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ChangeRequestReview)
            .join(ReviewRun, ChangeRequestReview.review_run_id == ReviewRun.id)
            .where(ReviewRun.project_id == project_id)
        )
        return count or 0


async def test_verify_review_baseline_and_trace(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_llm_resolver(
        monkeypatch,
        FakeLLM(
            content_sequence=["## 自己診断\n最重要: 特になし"],
            structured_by_schema=upstream_outputs(
                requirements_output(prev_id="FR-001", text="予約を登録し、確認メールを送る")
            ),
        ),
    )
    headers, user_id = await _login("cr-verify@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}/change-requests"
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    await client.post(base, json=_CR, headers=headers)
    await client.post(f"{base}/1/explain", headers=headers)
    await client.post(
        f"{base}/1/decision", json={**_DECISION, "decision": "agreed"}, headers=headers
    )
    await client.post(
        f"/api/v1/projects/{project_id}/generate",
        json={"start_from": "requirements", "cr_number": 1},
        headers=headers,
    )

    # 段階を承認していないので印は無く、文書の側の確認だけが残る
    verification = (await client.get(f"{base}/1/verification", headers=headers)).json()
    assert (verification["marks"], verification["can_verify"]) == ([], False)
    keys = [d["key"] for d in verification["documents"]]
    assert keys
    early = await client.post(f"{base}/1/verify", json={}, headers=headers)
    assert (early.status_code, early.json()["code"]) == (409, "CR_NOT_VERIFIABLE")

    run_id = await _review_run(project_id)
    candidates = (await client.get(f"{base}/1/review-candidates", headers=headers)).json()
    assert [c["review_run_id"] for c in candidates] == [str(run_id)]
    linked = await client.post(
        f"{base}/1/reviews", json={"review_run_id": str(run_id)}, headers=headers
    )
    assert linked.status_code == 200
    again = await client.post(
        f"{base}/1/reviews", json={"review_run_id": str(run_id)}, headers=headers
    )
    assert (again.status_code, again.json()["code"]) == (409, "CR_REVIEW_NOT_LINKABLE")

    checked = await client.put(
        f"{base}/1/document-checks", json={"keys": keys}, headers=headers
    )
    assert checked.json()["can_verify"] is True
    verified = await client.post(f"{base}/1/verify", json={"note": "確かめた"}, headers=headers)
    assert verified.json()["status"] == "verified"

    created = await client.post(
        f"/api/v1/projects/{project_id}/baselines",
        json={**_BASELINE, "change_request_numbers": [1]},
        headers=headers,
    )
    assert (created.status_code, created.json()["number"]) == (201, 2)
    trace = (await client.get(f"{base}/1/trace", headers=headers)).json()
    assert (trace["baseline_number"], trace["included_baseline_number"]) == (1, 2)
    assert [e["action"] for e in trace["entries"]] == [
        "create",
        "explain",
        "agree",
        "apply",
        "link_review",
        "verify",
        "baseline",
    ]
    assert all(d["available"] for d in trace["entries"][3]["documents"])
    assert trace["entries"][5]["reviews"][0]["review_run_id"] == str(run_id)
    assert await _links(project_id) == 1

    await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert await _rows(project_id) == (0, 0)
    assert await _links(project_id) == 0
