"""CR の反映で、書き換えてよい要素の合意を CR を出どころに更新するテスト。

SUT: app.agreements の change_request_items / kind_of(純粋関数)、
     AgreementService.record_change_request(app/services/agreement_service.py)と、
     DocGeneratorService.generate からの呼び出し(反映と同じトランザクション)
ドライバ: 各テスト関数
スタブ: 純粋関数のテストはスタブ不要 ── 副作用が無く外部依存を呼ばないため。流れのテストは、DB は
インメモリ SQLite(`db_session`)を本物のリポジトリで使い、文書の生成の LLM は FakeLLM(CR-1 の
反映は fixture の`reflect_change_request`。FR-001 を CR の文にした要件定義書と、外部設計書の見本)
"""

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_requests import (
    NEW_TEXT,
    agreed_change_request_project,
    reflect_change_request,
)
from tests.fixtures.external_design import upstream_outputs
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.requirements import requirements_output

from app.agreements import AgreedItem, change_request_items, kind_of
from app.documents.requirements.model import RequirementsModel
from app.models.agreement import AgreementRecord
from app.models.project import Project
from app.repositories.agreement import AgreementRepository
from app.repositories.change_request import ChangeRequestRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.services.agreement_service import AgreementService
from app.services.doc_generator_service import DocGeneratorService

_DIAGNOSIS = "## 自己診断\n最重要: 特になし"


def _item(key: str) -> AgreedItem:
    kind = kind_of(key)
    assert kind is not None
    return AgreedItem(key, kind, key, {})


# --- 純粋関数 ---


def test_kind_of_reads_the_key_prefix() -> None:
    assert kind_of("req:FR-001") == "requirement"
    assert kind_of("users") == "users"
    assert kind_of("screen:SCR-001") == "screen"
    assert kind_of("api:GET /x") == "api"
    assert kind_of("integration:メール配信") == "integration"
    assert kind_of("unit:U-001") is None


def test_change_request_items_update_present_and_remove_gone() -> None:
    """書き換えてよい要素のうち、作り直した文書にあるものは合意にし、無くなったものは外れた
    とする。CR で足された新しい要素・書き換えてよい要素の外は触らない。"""
    items = [_item("req:FR-001"), _item("req:FR-004"), _item("screen:SCR-001")]
    updated, removed = change_request_items(
        {"req:FR-001", "req:FR-002", "screen:SCR-001", "api:GET /x"},
        items,
        ["req:FR-001", "req:FR-002", "req:FR-003", "screen:SCR-001", "api:GET /x"],
        {"requirement", "users", "screen", "api", "integration"},
    )
    assert [i.key for i in updated] == ["req:FR-001", "screen:SCR-001"]
    assert removed == ["req:FR-002", "api:GET /x"]


def test_change_request_items_skip_documents_not_regenerated() -> None:
    """作り直さなかった文書の要素(ここでは外部設計書の API)は、無くても外れたとしない。"""
    _, removed = change_request_items(
        {"req:FR-002", "api:GET /x"},
        [],
        ["req:FR-002", "api:GET /x"],
        {"requirement", "users"},
    )
    assert removed == ["req:FR-002"]


# --- 流れ ---


async def _current(session: AsyncSession, project: Project) -> dict[str, AgreementRecord]:
    return {r.key: r for r in await AgreementRepository(session).current(project.id)}


async def _regenerate_without_cr(session: AsyncSession, project: Project) -> None:
    """CR を渡さない再生成(LLM は FR-001 を別の文に書き換える)。"""
    llm = FakeLLM(
        content_sequence=[_DIAGNOSIS, _DIAGNOSIS],
        structured_by_schema=upstream_outputs(
            requirements_output(prev_id="FR-001", text="書き換え")
        ),
    )
    service = DocGeneratorService(session)
    status = await service.request_generation(project, start_from="requirements")
    await service.generate(
        project.id,
        project.user_id,
        status_before_generation=status,
        start_from="requirements",
        llm=llm,
    )


async def test_smoke_applied_change_survives_plain_regeneration(
    db_session: AsyncSession,
) -> None:
    """CR-1(FR-001)を反映すると、FR-001 と予測の画面・API の合意が CR-1 を出どころに変わる。
    その後の CR を渡さない再生成で、FR-001 は束の文に戻らず CR の文のまま(Phase 92 の穴)。"""
    project = await agreed_change_request_project(db_session)
    change_request = await ChangeRequestRepository(db_session).get_by_number(
        project_id=project.id, number=1
    )
    assert change_request is not None

    await reflect_change_request(db_session, project)

    agreed = await _current(db_session, project)
    assert agreed["req:FR-001"].origin == "change_request"
    assert agreed["req:FR-001"].change_request_id == change_request.id
    assert (agreed["req:FR-001"].content or {})["text"] == NEW_TEXT
    assert agreed["screen:SCR-001"].origin == "change_request"
    assert agreed["api:POST /api/v1/reservations"].origin == "change_request"
    # 予測に無い要素と、CR の対象でない要件は束の合意のまま
    assert agreed["api:GET /api/v1/reservations"].origin == "baseline"
    assert agreed["req:FR-002"].origin == "baseline"

    await _regenerate_without_cr(db_session, project)

    document = await GeneratedDocumentRepository(db_session).get_current(
        project_id=project.id, doc_type="requirements"
    )
    assert document is not None
    model = RequirementsModel.model_validate(document.model)
    assert model.rows[0].text == NEW_TEXT
    assert {"key": "req:FR-001", "reason": "changed"} in (document.protection or [])


async def test_reapply_appends_records(db_session: AsyncSession) -> None:
    """やり直しの反映(反映済みのまま)は、そのときの中身で記録を足す(追記型)。"""
    project = await agreed_change_request_project(db_session)
    await reflect_change_request(db_session, project)
    await reflect_change_request(db_session, project)

    records = await AgreementRepository(db_session).list_for_project(project.id)
    fr = [r for r in records if r.key == "req:FR-001"]
    assert [r.origin for r in fr] == ["baseline", "change_request", "change_request"]


async def test_simple_mode_does_not_record(db_session: AsyncSession) -> None:
    project = await agreed_change_request_project(db_session)
    change_request = await ChangeRequestRepository(db_session).get_by_number(
        project_id=project.id, number=1
    )
    assert change_request is not None
    project.mode = "simple"

    records = await AgreementService(db_session).record_change_request(
        project, change_request, {"requirements": None}, recorded_by=None
    )
    assert records == []
