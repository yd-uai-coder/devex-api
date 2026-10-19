"""変更の説明書の任意の要約の、純粋な部分(LLM の入力・文の整え・状態)と表・リポジトリのテスト。

SUT: app.change_requests の build_summary_messages / clip_summary / summary_state と表示名、
     ChangeRequestSummaryRepository(get_for / replace)と ChangeRequestSummary の ORM 定義
ドライバ: pytest のテスト関数
スタブ: 純粋な部分は不要 ── 副作用が無く外部依存を呼ばないため。リポジトリは永続化そのものが
検証対象なので、テスト用の SQLite のセッションを使う(CR は fixture で作る)
"""

import uuid
from datetime import UTC, datetime

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project

from app.change_requests import (
    SUMMARY_MAX,
    SUMMARY_STATE_LABEL,
    build_summary_messages,
    clip_summary,
    summary_state,
)
from app.models.change_request_summary import ChangeRequestSummary
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.change_request_summary import ChangeRequestSummaryRepository
from app.schemas.baseline import BaselineCreate
from app.services.baseline_service import BaselineService


def test_messages_pass_only_the_explanation() -> None:
    messages = build_summary_messages("# 変更の説明書 CR-1\n\n## 1. 変更の内容と背景")

    assert isinstance(messages[0], SystemMessage) and "クライアント" in str(messages[0].content)
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "# 変更の説明書 CR-1\n\n## 1. 変更の内容と背景"


def test_clip_summary() -> None:
    assert clip_summary("  要約です。\n") == "要約です。"
    assert len(clip_summary("あ" * (SUMMARY_MAX + 10))) == SUMMARY_MAX


def test_summary_state_compares_fingerprints() -> None:
    assert summary_state("unconfirmed", "a", "a") == "unconfirmed"
    assert summary_state("confirmed", "a", "a") == "confirmed"
    assert summary_state("confirmed", "a", "b") == "stale"
    assert summary_state("unconfirmed", "a", "b") == "stale"
    assert set(SUMMARY_STATE_LABEL) == {"unconfirmed", "confirmed", "stale"}


async def _change_request_id(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    project = await create_impact_project(session)
    baseline = await BaselineService(session).create(
        project,
        BaselineCreate(agreed_at=datetime(2026, 10, 19, tzinfo=UTC), reason="合意"),
        recorded_by=None,
    )
    change_request = await ChangeRequestRepository(session).create(
        project_id=project.id,
        number=1,
        baseline_id=baseline.id,
        title="題",
        content="内容",
        background_source="meeting",
        status="draft",
        action="create",
    )
    return change_request.id, project.user_id


async def test_replace_creates_then_overwrites(db_session: AsyncSession) -> None:
    repository = ChangeRequestSummaryRepository(db_session)
    cr_id, user_id = await _change_request_id(db_session)
    assert await repository.get_for(cr_id) is None

    first = await repository.replace(
        change_request_id=cr_id,
        text="最初の要約",
        fingerprint="f1",
        status="unconfirmed",
        generated_model="gemini-3.5-flash-lite",
        generated_by=user_id,
    )
    first.text = "直した要約"
    first.status = "confirmed"
    first.confirmed_by = user_id
    await db_session.flush()

    second = await repository.replace(
        change_request_id=cr_id,
        text="作り直した要約",
        fingerprint="f2",
        status="unconfirmed",
        generated_model=None,
        generated_by=None,
    )

    assert second.id == first.id
    loaded = await repository.get_for(cr_id)
    assert loaded is not None
    assert (loaded.generated_text, loaded.text) == ("作り直した要約", "作り直した要約")
    assert (loaded.status, loaded.fingerprint, loaded.confirmed_by) == ("unconfirmed", "f2", None)
    assert loaded.generated_at is not None and loaded.edited_at is None


async def test_one_summary_per_change_request(db_session: AsyncSession) -> None:
    cr_id, _ = await _change_request_id(db_session)
    await ChangeRequestSummaryRepository(db_session).replace(
        change_request_id=cr_id,
        text="要約",
        fingerprint="f",
        status="unconfirmed",
        generated_model=None,
        generated_by=None,
    )
    db_session.add(
        ChangeRequestSummary(
            change_request_id=cr_id,
            generated_text="x",
            text="x",
            status="unconfirmed",
            fingerprint="f",
            generated_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
