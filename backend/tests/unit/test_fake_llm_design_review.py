"""E2E用の偽LLM(`E2eFakeLLM`)の出力で、設計レビュー(自動のレビュー・採否・差し戻し・聞き直し・
「直ったか」)とテスト仕様書を通す契約テスト。

ブラウザの E2E(devex-ui の `e2e/devex-flow.spec.ts` のレビューの流れ)と同じく、superuser が
レビューをオンにして4文書を生成し、自動のレビューの指摘を採用するところまでは画面でも見る。その先の
差し戻し(採用した指摘で再生成)・聞き直し・再生成の後のレビューと「直ったか」の判定は、画面を使わずに
サービスのメソッドで通す。詳細設計モードは、段階1〜7 の承認の後の段階の区切りのレビューと、
テスト仕様書を作るところまで。偽LLM の出力が、本物のレビューの保存・区切りの状態・追跡・予約札・
テスト仕様書の検証を通ることを固定する。

SUT: E2eFakeLLM のレビュー・判定・テスト仕様書の構造化出力(app/ai/llm/fake.py)と、それを受ける
     DocGeneratorService・ReviewService・FixCheckService の経路
ドライバ: テスト関数(画面の操作と同じ順に、サービスのメソッドを呼ぶ)
スタブ: FakeRedis(予約札と1日の回数。Redis の代わり)、_SendbackLLM(偽LLM を包み、修正指示を
     受けた外部設計書の生成でだけ画面の行を1つ変える ── 偽LLM は毎回同じ出力を返すため、指摘を
     直した版を作れない)。偽LLM そのものは検証される側(出力の出どころ)。DB はインメモリ SQLite
     (`db_session`)。
"""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.fake_llm_flow import (
    create_fake_detailed_project,
    generate_and_approve_stages_1_to_7,
)
from tests.fixtures.fake_redis import FakeRedis

from app.ai.llm.fake import E2eFakeLLM
from app.documents.external_design.drafting import ExternalDesignGenerationOutput
from app.documents.test_spec import validate_test_spec
from app.models.project import Project
from app.models.user import User
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.generated_document import TEST_SPEC
from app.repositories.review import ReviewRunRepository
from app.review import INSTRUCTION_HEAD
from app.services.chat_service import ChatService
from app.services.doc_generator_service import DocGeneratorService
from app.services.errors import SendbackFindingInvalidError
from app.services.fix_check_service import FixCheckService
from app.services.project import ProjectService
from app.services.review_service import ReviewService
from app.services.test_spec_state import current_test_spec

# 偽LLM のヒアリングが十分になるまでの発言(E2E の devex-flow.spec.ts と同じ数)
_HEARING_MESSAGES = [
    "利用者は倉庫の担当者を想定しています",
    "MVPでは在庫の入出庫記録と一覧表示のみ作ります",
    "特に技術的な制約はありません",
]

class _SendbackLLM(E2eFakeLLM):
    """修正指示(指摘の文の頭)を受けた外部設計書の生成でだけ、SCR-001 の操作に「取り消す」を
    足す偽LLM。"""

    def with_structured_output(self, schema, *, include_raw=False, method=None) -> Any:
        inner = super().with_structured_output(schema, include_raw=include_raw, method=method)
        if schema is not ExternalDesignGenerationOutput:
            return inner
        return _EditedExternal(inner)


class _EditedExternal:
    def __init__(self, inner) -> None:
        self._inner = inner

    async def ainvoke(self, messages: list[Any]) -> Any:
        # 生成は include_raw=True で呼ぶので、{"raw", "parsed", ...} の parsed を変える
        output = await self._inner.ainvoke(messages)
        if not any(INSTRUCTION_HEAD in str(m.content) for m in messages):
            return output
        edited = output["parsed"].model_copy(deep=True)
        edited.screens[0].ui_spec += "\n- 操作: 予約を取り消す"
        return {**output, "parsed": edited}


async def _heard_simple_project(
    session: AsyncSession, llm: E2eFakeLLM, *, review: bool
) -> tuple[Project, User]:
    """ヒアリングを終えた簡易モードのプロジェクト(`review`なら持ち主は superuser で、
    レビューがオン)。"""
    user = User(
        email="e2e-review@example.com",
        hashed_password="x",
        is_superuser=review,
        review_enabled=review,
    )
    session.add(user)
    await session.flush()
    project = await ProjectService(session).create(
        user_id=user.id,
        name="在庫管理",
        intake={
            "system_overview": "在庫管理システムを作りたい",
            "goals_raw": "在庫数をリアルタイムに可視化したい",
            "notes_raw": None,
            "environment": None,
        },
        files=[],
    )
    chat = ChatService(session)
    await chat.generate_opening_reply(project, llm=llm)
    for message in _HEARING_MESSAGES:
        async for _ in chat.stream_reply(project, user_message=message, card_answers=[], llm=llm):
            pass
    assert project.hearing_check is not None and project.hearing_check["is_sufficient"] is True
    return project, user


async def _generate(
    session: AsyncSession,
    project: Project,
    llm: E2eFakeLLM,
    redis: FakeRedis,
    **kwargs,
) -> None:
    """画面の「生成する」「再生成する」と同じく、受け付けてから実行する(実行は直接)。"""
    service = DocGeneratorService(session, redis=redis)  # type: ignore[arg-type]
    before = await service.request_generation(project, **kwargs)
    await service.generate(
        project.id,
        project.user_id,
        status_before_generation=before,
        llm=llm,
        review_llm=llm,
        judge_llm=llm,
        **kwargs,
    )
    await session.refresh(project)
    assert project.status == "completed"


async def test_smoke_simple_review_adopt_sendback_rehearing_and_fix_check(
    db_session: AsyncSession,
) -> None:
    llm = _SendbackLLM()
    redis = FakeRedis()
    project, user = await _heard_simple_project(db_session, llm, review=True)

    # 4文書とテスト仕様書を生成すると、自己診断の代わりに文書の区切りのレビューが流れる
    await _generate(db_session, project, llm, redis)
    spec = await current_test_spec(db_session, project)
    assert spec is not None and not spec.outdated
    assert [i.code for i in validate_test_spec(spec.model)] == []
    reviews = ReviewService(db_session)
    [gate] = await reviews.gate_summaries(project)
    assert (gate.gate, gate.status, gate.is_current) == ("documents", "reviewed", True)
    assert gate.run is not None and gate.run.trigger == "auto"
    # 指摘は重要度の順。テスト仕様書はレビューに渡さない
    assert [(f.level, f.cause) for f in gate.run.findings] == [
        ("critical", "design"),
        ("major", "hearing"),
    ]
    assert TEST_SPEC not in gate.run.targets
    design, hearing = gate.run.findings
    chat = [h.message for h in await ChatHistoryRepository(db_session).list_for_project(project.id)]
    assert not any("自己診断" in m for m in chat)
    assert "指摘 2件" in chat[-1]

    # 画面の「採用」。ヒアリングが原因の指摘は再生成に渡せず、聞き直しに使う
    for finding in (design, hearing):
        await reviews.decide(project, finding.id, "adopted")
    with pytest.raises(SendbackFindingInvalidError):
        await reviews.sendback_findings(project.id, [hearing.id], strict=True)

    # 採用した設計の指摘で、外部設計書から下流を作り直す(修正指示が生成のプロンプトに届くと、
    # 偽物が SCR-001 の行を変える)→ 同じ区切りでレビューが流れ直し、前の回の採用した指摘は
    # 「前の回で採用した指摘」に残り、対象の行が変わった指摘について「直ったか」を判定する
    await _generate(
        db_session, project, llm, redis, start_from="external_design", finding_ids=[design.id]
    )
    [gate] = await reviews.gate_summaries(project)
    assert gate.status == "reviewed"
    assert gate.run is not None and gate.run.id != design.run_id
    assert {f.id for f in gate.adopted_before} == {design.id, hearing.id}
    assert gate.followups[design.id] == "changed"
    assert gate.fix_checks[design.id].verdict == "fixed"

    # 同じ版で判定し直しても増えない(今の版で判定済み・予約札)
    assert await FixCheckService(db_session, redis=redis, llm=llm).run_for(project, user) == []  # type: ignore[arg-type]

    # 採用したヒアリングの指摘で聞き直す → 観点が未決に戻り、修正中になる
    view = await reviews.rehearing(project, [hearing.id])
    assert {p.key: p.status for p in view.points}["security"] == "open"
    assert project.status == "revising"


async def test_review_off_keeps_self_diagnosis(db_session: AsyncSession) -> None:
    llm = E2eFakeLLM()
    project, _ = await _heard_simple_project(db_session, llm, review=False)

    await _generate(db_session, project, llm, FakeRedis())

    # レビューの回は無く、今までどおり自己診断をチャットに出す
    [gate] = await ReviewService(db_session).gate_summaries(project)
    assert (gate.status, gate.run) == ("unreviewed", None)
    chat = [h.message for h in await ChatHistoryRepository(db_session).list_for_project(project.id)]
    assert "自己診断" in chat[-1]


async def test_detailed_stage_gates_and_test_spec(db_session: AsyncSession) -> None:
    llm = E2eFakeLLM()
    project = await create_fake_detailed_project(db_session, llm)
    user = await db_session.get(User, project.user_id)
    assert user is not None
    user.is_superuser = user.review_enabled = True
    await db_session.commit()
    reviews = ReviewService(db_session, llm=llm)

    # 段階1〜5 の承認では、段階の区切りはまだそろわない
    statuses = {g.gate: g.status for g in await reviews.gate_summaries(project)}
    assert statuses == {"documents": "unreviewed", "stage6": "not_ready", "stage7": "not_ready"}

    # 段階1〜7 を承認した後、承認のダイアログの「レビューする」と同じく段階6・7 の区切りを流す
    await generate_and_approve_stages_1_to_7(db_session, project, llm)
    for gate in ("stage6", "stage7"):
        run = await reviews.start_review(project, user, gate=gate, trigger="manual")
        finished = await reviews.execute(run.id)
        assert finished is not None
        assert finished.status == "succeeded", finished.error_code
        assert "stage:6" in finished.targets
    statuses = {g.gate: g.status for g in await reviews.gate_summaries(project)}
    assert statuses == {"documents": "unreviewed", "stage6": "reviewed", "stage7": "reviewed"}

    # テスト仕様書は段階1・7 の承認の後に、起点「test_spec」で作る(レビューは流さない)
    await _generate(db_session, project, llm, FakeRedis(), start_from=TEST_SPEC)
    spec = await current_test_spec(db_session, project)
    assert spec is not None and not spec.outdated
    assert [i.code for i in validate_test_spec(spec.model)] == []
    runs = ReviewRunRepository(db_session)
    assert await runs.get_latest(project_id=project.id, gate="documents") is None
