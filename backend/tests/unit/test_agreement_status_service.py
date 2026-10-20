"""表示中の版の合意の状態の API(サービス・ルート)のテスト。

SUT: AgreementService.status(app/services/agreement_service.py)、
     スキーマ AgreementStatusRead・agreement_status_read(app/schemas/agreement.py)、
     ルート get_agreement_status(app/api/routes/agreements.py)
ドライバ: 各テスト関数
スタブ: DB はインメモリ SQLite(`db_session`)を本物のリポジトリで使う。合意済みの版・CR の反映
までは tests/fixtures/change_requests.py(文書の生成の LLM は FakeLLM)
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project
from tests.fixtures.change_requests import (
    agreed_change_request_project,
    applied_change_request_project,
)

from app.api.routes.agreements import get_agreement_status
from app.models.project import Project
from app.repositories.generated_document import GeneratedDocumentRepository
from app.schemas.agreement import AgreementStatusRead
from app.services.agreement_service import AgreementService
from app.services.errors import DesignStagesNotAvailableError


async def test_everything_is_agreed_after_the_baseline(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)

    status = await AgreementService(db_session).status(project, "requirements")

    assert (status.version, status.change_request_number, status.restored) == (1, None, ())
    assert [(e.key, e.state, e.origin_label) for e in status.elements] == [
        ("req:FR-001", "agreed", "B-1"),
        ("req:FR-002", "agreed", "B-1"),
        ("req:NFR-001", "agreed", "B-1"),
    ]


async def test_change_request_version_shows_origin_and_restored(db_session: AsyncSession) -> None:
    """CR-1 を反映した版: 対象の FR-001 は CR-1 の合意。偽の LLM が落とした FR-002・NFR-001 は
    戻した(CR-1 の予測に無いので)。"""
    project = await applied_change_request_project(db_session)

    status = await AgreementService(db_session).status(project, "requirements")

    assert status.change_request_number == 1
    states = {e.key: (e.state, e.origin_label) for e in status.elements}
    assert states["req:FR-001"] == ("agreed", "CR-1")
    assert states["req:FR-002"] == ("agreed", "B-1")
    assert [(r.key, r.reason) for r in status.restored] == [
        ("req:FR-002", "dropped"),
        ("req:NFR-001", "dropped"),
    ]
    assert status.restored[0].label.startswith("FR-002")


async def test_restored_old_version_differs_from_the_agreement(db_session: AsyncSession) -> None:
    """CR-1 の前の版を表示中に戻すと、CR-1 で変えた FR-001 は「合意と違う」。"""
    project = await applied_change_request_project(db_session)
    await GeneratedDocumentRepository(db_session).set_current(
        project_id=project.id, doc_type="requirements", version=1
    )
    await db_session.commit()

    status = await AgreementService(db_session).status(project, "requirements")

    assert status.version == 1 and status.change_request_number is None
    states = {e.key: e.state for e in status.elements}
    assert states["req:FR-001"] == "differs"


async def test_route_returns_external_design_status(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)

    read = await get_agreement_status(db_session, project, "external_design")

    assert isinstance(read, AgreementStatusRead)
    assert read.doc_type == "external_design" and read.version == 1
    assert [(e.key, e.state_label) for e in read.elements] == [
        ("screen:SCR-001", "合意済み"),
        ("api:POST /api/v1/reservations", "合意済み"),
        ("api:GET /api/v1/reservations", "合意済み"),
    ]
    assert read.restored == []


async def test_unagreed_elements_and_restored_labels_in_the_read(
    db_session: AsyncSession,
) -> None:
    """合意済みの版の前は、どの要素も未合意(出どころなし)。戻した要素の理由の名前も載る。"""
    project = await create_impact_project(db_session)
    documents = GeneratedDocumentRepository(db_session)
    current = await documents.get_current(project_id=project.id, doc_type="requirements")
    assert current is not None
    current.protection = [{"key": "req:FR-001", "reason": "changed"}]
    await db_session.commit()

    read = await get_agreement_status(db_session, project, "requirements")

    assert {(e.state, e.origin_label) for e in read.elements} == {("unagreed", None)}
    assert [(r.key, r.reason_label) for r in read.restored] == [("req:FR-001", "書き換えを戻した")]


async def test_no_document_and_simple_mode(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    empty = Project(user_id=project.user_id, title="空", mode="detailed", status="hearing")
    simple = Project(user_id=project.user_id, title="簡易", mode="simple", status="completed")
    db_session.add_all([empty, simple])
    await db_session.commit()

    status = await AgreementService(db_session).status(empty, "external_design")
    assert (status.version, status.elements, status.restored) == (None, (), ())

    with pytest.raises(DesignStagesNotAvailableError):
        await AgreementService(db_session).status(simple, "requirements")
