"""変更要求(CR)のルートのテスト。

SUT: ルート create_change_request / list_change_requests / get_change_request /
     update_change_request / explain_change_request / decide_change_request /
     withdraw_change_request / copy_change_request / get_change_request_impact /
     get_change_request_explanation / download_change_request_explanation /
     generate_change_request_summary / edit_change_request_summary /
     confirm_change_request_summary / generate_change_request_option_draft /
     get_change_request_option_draft / adopt_change_request_option_draft
     (→ ChangeRequestService)と、api_router への登録
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ: ルートがサービスの結果を応答の形に詰めることが検証対象のため、テスト用の SQLite の
セッションと、合意済みの版を作った fixture(`create_impact_project`)を使う。要約だけは LLM の
解決(LlmResolver.resolve)を、固定の文を返すフェイクに差し替える(stub_llm_resolver)。選択肢の
下書きも同じ差し替えで、決まった構造化出力を返すフェイクを使う
"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.llm_resolver import stub_llm_resolver

from app.ai.llm.catalog import Purpose
from app.api.routes import api_router
from app.api.routes.change_requests import (
    adopt_change_request_option_draft,
    confirm_change_request_summary,
    copy_change_request,
    create_change_request,
    decide_change_request,
    download_change_request_explanation,
    edit_change_request_summary,
    explain_change_request,
    generate_change_request_option_draft,
    generate_change_request_summary,
    get_change_request,
    get_change_request_explanation,
    get_change_request_impact,
    get_change_request_option_draft,
    list_change_requests,
    update_change_request,
    withdraw_change_request,
)
from app.change_requests import OptionDraftItemOut, OptionDraftOut, OptionDraftTargetOut
from app.models.project import Project
from app.models.user import User
from app.schemas.baseline import BaselineCreate
from app.schemas.change_request import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestOptionsIn,
    ChangeRequestUpdate,
    ChangeRequestWithdraw,
    SummaryEdit,
)
from app.services.baseline_service import BaselineService
from app.services.errors import (
    ChangeRequestInvalidTransitionError,
    ChangeRequestNotFoundError,
    ChangeRequestSummaryNotFoundError,
)

_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
_CREATE = ChangeRequestCreate.model_validate(
    {
        "title": "一覧に絞り込みを足す",
        "content": "日付で絞り込む",
        "background_source": "email",
        "targets": [{"requirement_id": "FR-002"}],
    }
)


async def _setup(session: AsyncSession) -> tuple[Project, User]:
    project = await create_impact_project(session)
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AT, reason="合意"), recorded_by=None
    )
    user = await session.get(User, project.user_id)
    assert user is not None
    return project, user


async def test_routes_run_the_whole_flow(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)

    created = await create_change_request(_CREATE, db_session, project, user)
    edited = await update_change_request(
        1, ChangeRequestUpdate(content="日付と備品で絞り込む"), db_session, project, user
    )
    explained = await explain_change_request(1, db_session, project, user)
    rejected = await decide_change_request(
        1,
        ChangeRequestDecision(decision="rejected", decided_at=_AT, reason="範囲の外"),
        db_session,
        project,
        user,
    )
    copied = await copy_change_request(1, db_session, project, user)
    withdrawn = await withdraw_change_request(
        2, ChangeRequestWithdraw(reason="出し直さない"), db_session, project, user
    )
    listed = await list_change_requests(db_session, project)
    fetched = await get_change_request(1, db_session, project)

    assert (created.label, created.created_by, created.background_source_label) == (
        "CR-1",
        user.id,
        "メール",
    )
    assert edited.content == "日付と備品で絞り込む"
    assert explained.status == "explained"
    assert rejected.decision is not None and rejected.decision.recorded_by == user.id
    assert rejected.allowed_actions == ["copy"]
    assert (copied.label, copied.derived_from_number) == ("CR-2", 1)
    assert withdrawn.status_label == "取り下げ"
    assert [c.label for c in listed] == ["CR-2", "CR-1"]
    # 一覧も合意の記録を持つ(束の作成で、合意の記録の初期値にする)
    assert listed[1].decision is not None and listed[1].decision.reason == "範囲の外"
    assert listed[0].decision is None and listed[0].included_baseline_number is None
    assert fetched.derived_numbers == [2]
    assert [e.action for e in fetched.events] == ["create", "edit", "explain", "reject", "copied"]


async def test_routes_raise_for_missing_and_invalid(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    with pytest.raises(ChangeRequestNotFoundError):
        await get_change_request(5, db_session, project)
    with pytest.raises(ChangeRequestInvalidTransitionError):
        await copy_change_request(1, db_session, project, user)


async def test_impact_route_predicts_then_returns_saved(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    before = await get_change_request_impact(1, db_session, project)
    await explain_change_request(1, db_session, project, user)
    await decide_change_request(
        1,
        ChangeRequestDecision(decision="agreed", decided_at=_AT, reason="合う"),
        db_session,
        project,
        user,
    )
    after = await get_change_request_impact(1, db_session, project)

    assert (before.label, before.baseline_label, before.saved) == ("CR-1", "B-1", False)
    assert [(t.requirement_id, t.kind_label, t.text) for t in before.targets] == [
        ("FR-002", "内容の変更", "予約の一覧を見られる")
    ]
    assert {"screen:SCR-001", "fn:F-02", "unit:U-003"} <= {i.key for i in before.items}
    assert [(e.key, e.state) for e in before.excluded] == [("test_spec", "absent")]
    assert after.saved is True
    assert after.items == before.items
    with pytest.raises(ChangeRequestNotFoundError):
        await get_change_request_impact(9, db_session, project)


async def test_explanation_routes(db_session: AsyncSession) -> None:
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    preview = await get_change_request_explanation(1, db_session, project)
    download = await download_change_request_explanation(1, db_session, project)

    assert (preview.label, preview.filename) == ("CR-1", "cr-1-explanation.md")
    assert preview.markdown.startswith("# 変更の説明書 CR-1: 一覧に絞り込みを足す")
    assert len(preview.fingerprint) == 64
    assert preview.has_test_spec is False
    assert preview.units == ["予約の一覧を見る(M-01-T03)"]
    scale = {s.kind: s for s in preview.scale}
    assert (scale["unit"].label, scale["unit"].count) == ("作業単位", 1)
    assert scale["test"].count is None
    assert [r.code for r in scale["test"].uncountable] == ["document_missing"]
    assert download.media_type == "text/markdown"
    assert bytes(download.body).decode() == preview.markdown + "\n"
    assert "cr-1-explanation.md" in download.headers["content-disposition"]
    with pytest.raises(ChangeRequestNotFoundError):
        await get_change_request_explanation(9, db_session, project)


async def test_summary_routes(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """要約は利用者の生成の用途で解決した LLM で作り、照合の結果と状態を返す。確かめると
    ダウンロードに載る。"""
    stub = stub_llm_resolver(monkeypatch, FakeLLM(content="FR-002 と FR-009 を40日で直します。"))
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    with pytest.raises(ChangeRequestSummaryNotFoundError):
        await edit_change_request_summary(1, SummaryEdit(text="要約"), db_session, project)
    generated = await generate_change_request_summary(1, db_session, project, user)
    assert stub.calls == [(user.id, Purpose.GENERATION)]
    assert generated.summary is not None
    assert (generated.summary.state, generated.summary.state_label) == ("unconfirmed", "未確認")
    assert generated.summary.unknown_requirement_ids == ["FR-009"]
    assert generated.summary.unknown_numbers == ["40"]
    assert generated.summary.needs_reconfirm is False
    assert generated.summary.generated_model == "gemini-3.5-flash-lite"
    assert generated.output == generated.markdown + "\n"

    edited = await edit_change_request_summary(
        1, SummaryEdit(text="FR-002 の一覧を直します。"), db_session, project
    )
    assert edited.summary is not None and edited.summary.edited is True
    assert edited.summary.unknown_requirement_ids == []
    confirmed = await confirm_change_request_summary(1, db_session, project, user)
    assert confirmed.summary is not None and confirmed.summary.state == "confirmed"
    assert confirmed.summary.confirmed_by == user.id
    download = await download_change_request_explanation(1, db_session, project)
    assert "## 要約\n\nFR-002 の一覧を直します。" in bytes(download.body).decode()


async def test_option_routes(db_session: AsyncSession) -> None:
    """作成・更新・合意のルートが案を受け取り、詳細に案と選んだ番号を出す。説明書の応答は、合意の
    前に案ごとの規模を出し、案を選んだ後は出さない。"""
    project, user = await _setup(db_session)
    options = {
        "items": [
            {"name": "日付だけ", "targets": [{"requirement_id": "FR-002"}], "outcome": "小さい"},
            {"name": "日付と備品", "targets": [{"requirement_id": "FR-002"}], "outcome": "大きい"},
        ]
    }

    created = await create_change_request(
        _CREATE.model_copy(update={"options": ChangeRequestOptionsIn.model_validate(options)}),
        db_session,
        project,
        user,
    )
    edited = await update_change_request(
        1,
        ChangeRequestUpdate.model_validate(
            {"options": {**options, "recommended": 2, "reason": "漏れが無い"}}
        ),
        db_session,
        project,
        user,
    )
    choosing = await get_change_request_explanation(1, db_session, project)
    await explain_change_request(1, db_session, project, user)
    agreed = await decide_change_request(
        1,
        ChangeRequestDecision(decision="agreed", decided_at=_AT, reason="合う", option=2),
        db_session,
        project,
        user,
    )
    chosen = await get_change_request_explanation(1, db_session, project)

    assert [(o.label, o.name, o.recommended) for o in choosing.option_scales] == [
        ("案1", "日付だけ", False),
        ("案2", "日付と備品", True),
    ]
    assert choosing.option_scales[0].scale[0].kind == "unit"
    assert "## 3. 選択肢" in choosing.markdown
    assert chosen.option_scales == []
    assert "合意した案: 案2 日付と備品。" in chosen.markdown
    assert created.options is not None
    assert [o.label for o in created.options.items] == ["案1", "案2"]
    assert edited.options is not None and edited.options.recommended == 2
    assert agreed.decision is not None and agreed.decision.option == 2


async def test_option_draft_routes(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """下書きは利用者の生成の用途で解決した LLM で作り、照合の結果を返す。取り込むと CR の案に
    なる。"""
    draft = OptionDraftOut(
        options=[
            OptionDraftItemOut(
                name=name,
                targets=[OptionDraftTargetOut(requirement_id="FR-002")],
                outcome=outcome,
            )
            for name, outcome in (("日付だけ", "小さい"), ("日付と備品", "40日かかる"))
        ],
        recommended=1,
        reason="早い",
    )
    stub = stub_llm_resolver(monkeypatch, FakeLLM(structured=draft))
    project, user = await _setup(db_session)
    await create_change_request(_CREATE, db_session, project, user)

    generated = await generate_change_request_option_draft(1, db_session, project, user)
    read = await get_change_request_option_draft(1, db_session, project)
    adopted = await adopt_change_request_option_draft(1, db_session, project, user)
    after = await get_change_request_option_draft(1, db_session, project)

    assert stub.calls == [(user.id, Purpose.GENERATION)]
    assert (generated.status, generated.status_label) == ("unconfirmed", "未確認")
    assert generated.generated_model == "gemini-3.5-flash-lite"
    assert [o.label for o in read.options.items] == ["案1", "案2"]
    assert read.unknown_numbers == ["40"]
    assert read.adoptable is True and read.problems == []
    assert read.stale is False
    assert adopted.options is not None and adopted.options.recommended == 1
    assert (after.status, after.stale) == ("adopted", True)


def test_router_is_registered() -> None:
    """api_router(app/api/routes/__init__.py)に CR のルーターを登録したこと。"""
    app = FastAPI()
    app.include_router(api_router)
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/change-requests" in paths
    assert "/projects/{project_id}/change-requests/{number}/decision" in paths
    assert "/projects/{project_id}/change-requests/{number}/copy" in paths
    assert "/projects/{project_id}/change-requests/{number}/impact" in paths
    assert "/projects/{project_id}/change-requests/{number}/explanation" in paths
    assert "/projects/{project_id}/change-requests/{number}/explanation/download" in paths
    assert "/projects/{project_id}/change-requests/{number}/explanation/summary" in paths
    assert "/projects/{project_id}/change-requests/{number}/explanation/summary/confirm" in paths
    assert "/projects/{project_id}/change-requests/{number}/comparison" in paths
    assert "/projects/{project_id}/change-requests/{number}/options/draft" in paths
    assert "/projects/{project_id}/change-requests/{number}/options/draft/adopt" in paths
    assert "/projects/{project_id}/change-requests/{number}/verification" in paths
    assert "/projects/{project_id}/change-requests/{number}/document-checks" in paths
    assert "/projects/{project_id}/change-requests/{number}/verify" in paths
    assert "/projects/{project_id}/change-requests/{number}/review-candidates" in paths
    assert "/projects/{project_id}/change-requests/{number}/reviews" in paths
    assert "/projects/{project_id}/change-requests/{number}/trace" in paths
