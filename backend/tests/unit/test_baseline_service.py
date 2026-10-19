"""合意済みの版(束)の作成・一覧・取得のテスト。

SUT: BaselineService(create / list_for_project / get)と、写しを作る純粋関数
     document_snapshots / stage_snapshots、ルート create_baseline / list_baselines /
     get_baseline、BaselineCreate、DesignStageService.stage_rows
ドライバ: pytest のテスト関数(ルートは関数を直接呼ぶ)
スタブ不要 ── LLM を呼ばず、永続化と状態の判定が検証対象のため、テスト用の SQLite のセッションと、
段階1〜8 を承認済みにした fixture(`create_impact_project`)を使う
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project

from app.api.routes.baselines import create_baseline, get_baseline, list_baselines
from app.models.design_stage import DesignStage
from app.models.project import Project
from app.models.user import User
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.baseline import BaselineCreate
from app.services.baseline_service import BaselineService
from app.services.design_stage_service import DesignStageService
from app.services.errors import (
    BaselineDocumentsMissingError,
    BaselineNotFoundError,
    DesignStageGenerationInProgressError,
    DesignStagesNotAvailableError,
    DocGenerationInProgressError,
)

_AGREED_AT = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
_DOCUMENT_KEYS = ["requirements", "external_design", "test_spec"]
_STAGE_KEYS = [f"stage:{n}" for n in range(1, 9)]


def _payload(**overrides: object) -> BaselineCreate:
    values: dict[str, object] = {
        "counterpart": "山田様",
        "agreed_at": _AGREED_AT,
        "memo": "定例で確認",
        "reason": "要件と外部設計の合意",
    }
    values.update(overrides)
    return BaselineCreate.model_validate(values)


async def _stage(session: AsyncSession, project: Project, stage: int) -> DesignStage:
    row = await session.scalar(
        select(DesignStage).where(DesignStage.project_id == project.id, DesignStage.stage == stage)
    )
    assert row is not None
    return row


async def _user(session: AsyncSession, project: Project) -> User:
    user = await session.get(User, project.user_id)
    assert user is not None
    return user


async def test_create_route_copies_documents_and_stages(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    user = await _user(db_session, project)

    read = await create_baseline(_payload(), db_session, project, user)

    assert (read.number, read.label, read.counterpart, read.recorded_by) == (
        1,
        "B-1",
        "山田様",
        user.id,
    )
    assert [item.key for item in read.items] == _DOCUMENT_KEYS + _STAGE_KEYS
    by_key = {item.key: item for item in read.items}
    requirements = by_key["requirements"]
    assert (requirements.included, requirements.state, requirements.source_version) == (
        True,
        "present",
        1,
    )
    assert requirements.content == "# requirements"
    assert requirements.model is not None
    assert by_key["test_spec"].included is False
    assert by_key["test_spec"].state == "absent"
    stage1 = by_key["stage:1"]
    assert (stage1.included, stage1.state, stage1.source_version) == (True, "approved", 1)
    assert stage1.model == (await _stage(db_session, project, 1)).approved_model


async def test_snapshot_is_frozen_against_later_changes(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    service = BaselineService(db_session)
    created = await service.create(project, _payload(), recorded_by=None)
    original_stage1 = dict((await _stage(db_session, project, 1)).approved_model or {})

    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="requirements", content="# changed", model={"rows": []}
    )
    row = await _stage(db_session, project, 1)
    row.approved_model = {"functions": []}
    row.approved_version = 2
    await db_session.commit()
    db_session.expunge_all()

    loaded = await BaselineService(db_session).get(project, created.number)
    by_key = {item.key: item for item in loaded.items}
    assert by_key["requirements"].content == "# requirements"
    assert by_key["requirements"].source_version == 1
    assert by_key["stage:1"].model == original_stage1
    assert by_key["stage:1"].source_version == 1


async def test_unapproved_and_outdated_stages_are_recorded(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    unapproved = await _stage(db_session, project, 7)
    unapproved.status = "draft"
    unapproved.approved_model = None
    unapproved.approved_version = None
    outdated = await _stage(db_session, project, 8)
    outdated.input_fingerprint = {"doc:requirements": 99}
    await db_session.commit()

    rows, views = await DesignStageService(db_session).stage_rows(project)
    baseline = await BaselineService(db_session).create(project, _payload(), recorded_by=None)

    assert sorted(rows) == list(range(1, 9))
    assert views[8].state == "outdated"
    by_key = {item.key: item for item in baseline.items}
    assert (by_key["stage:7"].included, by_key["stage:7"].state) == (False, "draft")
    assert by_key["stage:7"].model is None
    assert (by_key["stage:8"].included, by_key["stage:8"].state) == (True, "outdated")


async def test_test_spec_and_generated_model_are_copied(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id,
        doc_type="test_spec",
        content="# test_spec",
        model={"cases": []},
        generated_model="m-1",
    )
    await db_session.commit()

    baseline = await BaselineService(db_session).create(project, _payload(), recorded_by=None)

    test_spec = next(item for item in baseline.items if item.key == "test_spec")
    assert (test_spec.included, test_spec.content, test_spec.generated_model) == (
        True,
        "# test_spec",
        "m-1",
    )


async def test_list_and_get_routes(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    service = BaselineService(db_session)
    for reason in ("一回目", "二回目"):
        await service.create(project, _payload(reason=reason), recorded_by=None)

    listed = await list_baselines(db_session, project)
    second = await get_baseline(2, db_session, project)

    assert [b.label for b in listed] == ["B-2", "B-1"]
    assert not hasattr(listed[0].items[0], "content")
    assert second.reason == "二回目"
    with pytest.raises(BaselineNotFoundError):
        await get_baseline(3, db_session, project)


async def test_simple_mode_is_rejected(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    project.mode = "simple"

    with pytest.raises(DesignStagesNotAvailableError):
        await BaselineService(db_session).create(project, _payload(), recorded_by=None)


async def test_generating_is_rejected(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    service = BaselineService(db_session)
    project.status = "generating"
    with pytest.raises(DocGenerationInProgressError):
        await service.create(project, _payload(), recorded_by=None)

    project.status = "completed"
    (await _stage(db_session, project, 5)).generation_status = "generating"
    with pytest.raises(DesignStageGenerationInProgressError):
        await service.create(project, _payload(), recorded_by=None)


async def test_missing_documents_are_rejected(db_session: AsyncSession) -> None:
    user = User(email="no-docs@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    project = Project(user_id=user.id, title="p", mode="detailed", status="completed")
    db_session.add(project)
    await db_session.flush()
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="requirements", content="# r"
    )

    with pytest.raises(BaselineDocumentsMissingError):
        await BaselineService(db_session).create(project, _payload(), recorded_by=None)


def test_payload_normalizes_and_validates() -> None:
    assert _payload(counterpart="  ").counterpart is None
    assert _payload(reason="  理由 ").reason == "理由"
    with pytest.raises(ValidationError):
        _payload(reason=" ")
    with pytest.raises(ValidationError):
        _payload(agreed_at=datetime(2026, 10, 19, 10, 0))  # タイムゾーンなし
