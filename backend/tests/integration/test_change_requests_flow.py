"""変更要求(CR)の API を実際の PostgreSQL で確かめる(JSONB の対象と予測・時系列・自己参照・
説明書と要約・CASCADE)。

SUT: POST・GET /api/v1/projects/{id}/change-requests、GET・PATCH .../{number}、
     POST .../{number}/explain・decision・withdraw・copy、GET .../{number}/impact、
     GET .../{number}/explanation・explanation/download、POST・PATCH .../explanation/summary、
     POST .../explanation/summary/confirm、CR を渡した POST /api/v1/projects/{id}/generate と
     GET .../{number}/comparison(反映の前 → 後の`reflections`も)、GET .../{number}/verification・
     PUT .../document-checks・POST .../verify、GET .../review-candidates・POST .../reviews、
     重なる CR の競合(CR の`conflicts`・POST .../{number}/conflict-checks・反映の409)、束の作成の
     change_request_numbers、GET .../{number}/trace、新しい要件の予測の起点
     (GET /api/v1/projects/{id}/baselines/{number}/origins と CR の`origins`)、選択肢
     (CR の`options`・合意の`option`、POST・GET .../{number}/options/draft・
     POST .../options/draft/adopt)、共有リンク(POST・GET .../{number}/share-links、
     POST .../share-links/{id}/revoke・extend、ログインの要らない GET /api/v1/share/{token})、
     コメント欄と共有リンクの判断(POST /api/v1/share/{token}/comments・decision、
     GET .../{number}/comments・POST .../comments/{id}/replies)
     (ルート → ChangeRequestService・
     DocGeneratorService・BaselineService → ChangeRequestRepository・
     ChangeRequestSummaryRepository・ChangeRequestOptionDraftRepository・
     GeneratedDocumentRepository・ReviewRunRepository)と、プロジェクトの削除での CR・時系列・
     要約・選択肢の下書き・共有リンク・コメント・結んだレビューの CASCADE
ドライバ: httpx の AsyncClient(ASGITransport)
スタブ: 要約・選択肢の下書きと文書の生成の LLM だけ、解決(LlmResolver.resolve)を固定の文・
構造化出力の見本を返すフェイクに差し替える(stub_llm_resolver)。ほかは LLM を呼ばない。
利用者・プロジェクト・文書はセッションで直接作り、アクセストークンも直接発行する(API で登録
すると、スイート全体で登録・ログインの IP ごとの上限に当たるため)
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

from app.change_requests import OptionDraftItemOut, OptionDraftOut, OptionDraftTargetOut
from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.change_request import ChangeRequest, ChangeRequestEvent, ChangeRequestReview
from app.models.change_request_comment import ChangeRequestComment
from app.models.change_request_option_draft import ChangeRequestOptionDraft
from app.models.change_request_share_link import ChangeRequestShareLink
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
    # 3節の規模(種類の順。数えられないときは count が null で、理由が付く)
    assert [s["kind"] for s in preview["scale"]] == ["unit", "file", "test", "screen", "api"]
    assert all(s["count"] is not None or s["uncountable"] for s in preview["scale"])

    generated = await client.post(f"{base}/1/explanation/summary", headers=headers)
    assert generated.status_code == 200
    assert generated.json()["summary"]["state"] == "unconfirmed"
    assert generated.json()["summary"]["needs_reconfirm"] is False
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


async def test_options_draft_adopt_and_agree(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM の下書きを取り込んで案にし(JSONB)、説明書に選択肢の節と案ごとの規模が出る。合意は
    案を選び、選んだ案の対象が CR の対象になり、予測もその対象で保存する。下書きはプロジェクトの
    削除で消える。"""
    draft = OptionDraftOut(
        options=[
            OptionDraftItemOut(
                name="登録だけ",
                targets=[OptionDraftTargetOut(requirement_id="FR-001")],
                outcome="手間が小さい",
            ),
            OptionDraftItemOut(
                name="登録と一覧",
                targets=[
                    OptionDraftTargetOut(requirement_id="FR-001"),
                    OptionDraftTargetOut(requirement_id="FR-002"),
                ],
                outcome="一覧も直す",
            ),
        ],
        recommended=1,
        reason="早い",
    )
    stub_llm_resolver(monkeypatch, FakeLLM(structured=draft))
    headers, user_id = await _login("cr-options@example.com")
    project_id = await _project(user_id)
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    base = f"/api/v1/projects/{project_id}/change-requests"
    targets = [{"requirement_id": "FR-001"}, {"requirement_id": "FR-002"}]
    await client.post(base, json={**_CR, "targets": targets}, headers=headers)

    missing = await client.get(f"{base}/1/options/draft", headers=headers)
    assert (missing.status_code, missing.json()["code"]) == (404, "CR_OPTION_DRAFT_NOT_FOUND")
    generated = await client.post(f"{base}/1/options/draft", headers=headers)
    assert generated.status_code == 200
    assert (generated.json()["status"], generated.json()["adoptable"]) == ("unconfirmed", True)
    adopted = await client.post(f"{base}/1/options/draft/adopt", headers=headers)
    assert adopted.status_code == 200
    assert [o["name"] for o in adopted.json()["options"]["items"]] == ["登録だけ", "登録と一覧"]

    preview = (await client.get(f"{base}/1/explanation", headers=headers)).json()
    assert "## 3. 選択肢" in preview["markdown"]
    assert [o["label"] for o in preview["option_scales"]] == ["案1", "案2"]

    await client.post(f"{base}/1/explain", headers=headers)
    agreed = {**_DECISION, "decision": "agreed"}
    no_option = await client.post(f"{base}/1/decision", json=agreed, headers=headers)
    assert (no_option.status_code, no_option.json()["code"]) == (422, "CR_OPTION_INVALID")
    decided = await client.post(f"{base}/1/decision", json={**agreed, "option": 1}, headers=headers)
    assert decided.status_code == 200
    assert decided.json()["decision"]["option"] == 1
    assert [t["requirement_id"] for t in decided.json()["targets"]] == ["FR-001"]
    impact = (await client.get(f"{base}/1/impact", headers=headers)).json()
    assert [t["requirement_id"] for t in impact["targets"]] == ["FR-001"]

    request_ids = await _request_ids(project_id)
    async with AsyncSessionLocal() as session:
        row = await session.get(ChangeRequest, request_ids[0])
        assert row is not None and row.decision_option == 1
        assert row.options is not None and row.options["recommended"] == 1
    await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ChangeRequestOptionDraft)
            .where(ChangeRequestOptionDraft.change_request_id.in_(request_ids))
        )
    assert count == 0


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
    assert [r["basis"] for r in comparison["scale"]] == ["marks"] * 3 + ["document_checks"] * 2
    # 反映ごとの要素の前 → 後
    [reflection] = comparison["reflections"]
    assert reflection["recorded"] is True
    fr001 = next(c for c in reflection["changes"] if c["key"] == "req:FR-001")
    assert (fr001["change"], fr001["scope"]) == ("changed", "target")

    await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert await _rows(project_id) == (0, 0)


async def test_conflicting_cr_needs_check_before_apply(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CR-1 を反映した後、同じ要件を対象にする CR-2 の反映は、競合を確かめるまで409。"""
    stub_llm_resolver(
        monkeypatch,
        FakeLLM(
            content_sequence=["## 自己診断\n最重要: 特になし"] * 2,
            structured_by_schema=upstream_outputs(
                requirements_output(prev_id="FR-001", text="予約を登録し、確認メールを送る")
            ),
        ),
    )
    headers, user_id = await _login("cr-conflict@example.com")
    project_id = await _project(user_id)
    base = f"/api/v1/projects/{project_id}/change-requests"
    generate = f"/api/v1/projects/{project_id}/generate"
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    for number in (1, 2):
        await client.post(base, json=_CR, headers=headers)
        await client.post(f"{base}/{number}/explain", headers=headers)
        await client.post(
            f"{base}/{number}/decision", json={**_DECISION, "decision": "agreed"}, headers=headers
        )
    applied = await client.post(
        generate, json={"start_from": "requirements", "cr_number": 1}, headers=headers
    )
    assert applied.status_code == 202

    detail = (await client.get(f"{base}/2", headers=headers)).json()
    [conflict] = detail["conflicts"]
    assert (conflict["requirement_id"], conflict["by"], conflict["checked"]) == (
        "FR-001",
        ["CR-1"],
        False,
    )
    blocked = await client.post(
        generate, json={"start_from": "requirements", "cr_number": 2}, headers=headers
    )
    assert (blocked.status_code, blocked.json()["code"]) == (409, "CR_CONFLICT_UNCHECKED")
    none = await client.post(f"{base}/1/conflict-checks", headers=headers)
    assert none.status_code == 409

    checked = await client.post(f"{base}/2/conflict-checks", headers=headers)
    assert checked.status_code == 200
    assert checked.json()["conflicts"][0]["checked"] is True
    accepted = await client.post(
        generate, json={"start_from": "requirements", "cr_number": 2}, headers=headers
    )
    assert accepted.status_code == 202

    await client.delete(f"/api/v1/projects/{project_id}", headers=headers)


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


async def _share_links(project_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ChangeRequestShareLink)
            .join(ChangeRequest)
            .where(ChangeRequest.project_id == project_id)
        )
        return count or 0


async def test_share_link_flow(client: AsyncClient) -> None:
    """共有リンクを作ると説明済みになり、ログインなしで読める。直すと止まり、説明し直すと前回から
    変わった節を出す。取り消すと410、無いトークンは404。プロジェクトの削除でリンクも消える。"""
    headers, user_id = await _login("cr-share@example.com")
    project_id = await _project(user_id)
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    base = f"/api/v1/projects/{project_id}/change-requests"
    await client.post(base, json=_CR, headers=headers)

    created = await client.post(f"{base}/1/share-links", json={}, headers=headers)
    assert created.status_code == 201
    token = created.json()["token"]
    assert created.json()["path"] == f"/share/{token}"
    detail = (await client.get(f"{base}/1", headers=headers)).json()
    assert detail["status"] == "explained"
    assert "markdown" not in detail["events"][-1]["refs"]

    opened = await client.get(f"/api/v1/share/{token}")
    assert opened.status_code == 200
    assert opened.json()["state"] == "open"
    assert opened.json()["markdown"].startswith("# 変更の説明書 CR-1")

    await client.patch(f"{base}/1", json={"content": "3つから10へ増やす"}, headers=headers)
    paused = (await client.get(f"/api/v1/share/{token}")).json()
    assert (paused["state"], paused["markdown"]) == ("paused", None)
    await client.post(f"{base}/1/explain", headers=headers)
    reopened = (await client.get(f"/api/v1/share/{token}")).json()
    assert [c["name"] for c in reopened["changes"]] == ["変更の内容と背景"]

    listed = (await client.get(f"{base}/1/share-links", headers=headers)).json()
    assert listed[0]["last_viewed_at"] is not None
    link_id = listed[0]["id"]
    extended = await client.post(
        f"{base}/1/share-links/{link_id}/extend", json={"expires_in_days": 30}, headers=headers
    )
    assert extended.status_code == 200
    revoked = await client.post(f"{base}/1/share-links/{link_id}/revoke", headers=headers)
    assert revoked.json()["state"] == "revoked"
    gone = await client.get(f"/api/v1/share/{token}")
    assert (gone.status_code, gone.json()["code"]) == (410, "SHARE_LINK_REVOKED")
    missing = await client.get("/api/v1/share/no-such-token")
    assert (missing.status_code, missing.json()["code"]) == (404, "SHARE_LINK_NOT_FOUND")
    trace = (await client.get(f"{base}/1/trace", headers=headers)).json()
    assert [e["action"] for e in trace["entries"]][-2:] == ["extend_share", "unshare"]

    assert await _share_links(project_id) == 1
    await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert await _share_links(project_id) == 0



async def _comments(project_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ChangeRequestComment)
            .join(ChangeRequest)
            .where(ChangeRequest.project_id == project_id)
        )
        return count or 0


async def test_share_comment_and_decision_flow(client: AsyncClient) -> None:
    """ログインなしでコメントし、開発者が返信する。共有リンクで合意すると CR の判断になり(相手 =
    書いた名前・出どころ・見た版)、開発者の判断は409。プロジェクトの削除でコメントも消える。"""
    headers, user_id = await _login("cr-share-write@example.com")
    project_id = await _project(user_id)
    await client.post(f"/api/v1/projects/{project_id}/baselines", json=_BASELINE, headers=headers)
    base = f"/api/v1/projects/{project_id}/change-requests"
    await client.post(base, json=_CR, headers=headers)
    token = (await client.post(f"{base}/1/share-links", json={}, headers=headers)).json()["token"]
    fingerprint = (await client.get(f"/api/v1/share/{token}")).json()["fingerprint"]

    commented = await client.post(
        f"/api/v1/share/{token}/comments",
        json={"name": "山田", "body": "5つで足ります", "fingerprint": fingerprint},
    )
    assert commented.status_code == 201
    listed = (await client.get(f"{base}/1/comments", headers=headers)).json()
    replied = await client.post(
        f"{base}/1/comments/{listed[0]['id']}/replies",
        json={"body": "承知しました"},
        headers=headers,
    )
    assert replied.status_code == 201
    stale = await client.post(
        f"/api/v1/share/{token}/decision",
        json={"decision": "agreed", "name": "山田", "fingerprint": "0" * 64},
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "SHARE_EXPLANATION_CHANGED")
    decided = await client.post(
        f"/api/v1/share/{token}/decision",
        json={"decision": "agreed", "name": "山田", "fingerprint": fingerprint},
    )
    assert decided.status_code == 200
    body = decided.json()
    assert body["state"] == "decided"
    assert (body["decision"]["counterpart"], body["decision"]["source"]) == ("山田", "share_link")
    assert [(c["author"], [r["author"] for r in c["replies"]]) for c in body["comments"]] == [
        ("山田", ["開発者"])
    ]

    detail = (await client.get(f"{base}/1", headers=headers)).json()
    assert detail["status"] == "agreed"
    assert detail["decision"]["fingerprint"] == fingerprint
    again = await client.post(f"{base}/1/decision", json=_DECISION, headers=headers)
    assert (again.status_code, again.json()["code"]) == (409, "CR_INVALID_TRANSITION")
    closed = await client.post(
        f"/api/v1/share/{token}/comments",
        json={"name": "山田", "body": "もう1つ", "fingerprint": fingerprint},
    )
    assert (closed.status_code, closed.json()["code"]) == (409, "SHARE_LINK_NOT_WRITABLE")

    assert await _comments(project_id) == 2
    await client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert await _comments(project_id) == 0
