"""束の作成・「採用にする」での合意の記録と、束が写した版を巡回で消さないことのテスト。

SUT: AgreementService(record_baseline / record_adopt)を、BaselineService.create と
     DocGeneratorService.adopt_requirement の経路で。GeneratedDocumentRepository.create_version の
     巡回(束が写した版を除く)
ドライバ: pytest のテスト関数
スタブ不要 ── LLM を呼ばない経路(束の作成・採用・版の追加)だけで、DB はテスト用の SQLite の
セッションを使う
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.change_impact import create_impact_project, impact_documents

from app.documents.requirements.model import RequirementRow
from app.models.project import Project
from app.repositories.agreement import AgreementRepository
from app.repositories.generated_document import (
    MAX_VERSIONS_PER_DOC_TYPE,
    GeneratedDocumentRepository,
)
from app.schemas.baseline import BaselineCreate
from app.services.agreement_service import AgreementService
from app.services.baseline_service import BaselineService
from app.services.doc_generator_service import DocGeneratorService

_AGREED_AT = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
_DOCUMENT_KEYS = [
    "req:FR-001",
    "req:FR-002",
    "req:NFR-001",
    "screen:SCR-001",
    "api:POST /api/v1/reservations",
    "api:GET /api/v1/reservations",
]


async def _create_baseline(session: AsyncSession, project: Project) -> None:
    await BaselineService(session).create(
        project, BaselineCreate(agreed_at=_AGREED_AT, reason="合意"), recorded_by=project.user_id
    )


async def _new_requirements(session: AsyncSession, project: Project, rows: list[dict]) -> None:
    """要件定義書の新しい版(行を差し替え)を作り、表示中にする。"""
    model = {**impact_documents()["requirements"], "rows": rows}
    await GeneratedDocumentRepository(session).create_version(
        project_id=project.id, doc_type="requirements", content="# r", model=model
    )
    await session.commit()


async def test_baseline_records_every_document_element(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    await _create_baseline(db_session, project)

    current = await AgreementRepository(db_session).current(project.id)
    assert [r.key for r in current] == _DOCUMENT_KEYS
    assert {r.origin for r in current} == {"baseline"}
    assert {r.recorded_by for r in current} == {project.user_id}
    assert current[0].baseline is not None and current[0].baseline.number == 1
    assert current[0].content is not None and current[0].content["text"] == "予約を登録できる"


async def test_next_baseline_records_removed_elements(db_session: AsyncSession) -> None:
    """束に入らなかった要素は「外れた」になり、今の合意から消える(記録は残る)。"""
    project = await create_impact_project(db_session)
    await _create_baseline(db_session, project)
    rows = impact_documents()["requirements"]["rows"]
    await _new_requirements(db_session, project, [rows[0], rows[2]])
    await _create_baseline(db_session, project)

    repo = AgreementRepository(db_session)
    current = await repo.current(project.id)
    assert "req:FR-002" not in [r.key for r in current]
    assert {r.baseline.number for r in current if r.baseline} == {2}
    removed = [r for r in await repo.list_for_project(project.id) if r.content is None]
    assert [(r.key, r.label, r.baseline_id is not None) for r in removed] == [
        ("req:FR-002", "FR-002 予約の一覧を見られる", True)
    ]


async def test_adopt_records_the_row_in_detailed_mode(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    rows = impact_documents()["requirements"]["rows"]
    await _new_requirements(db_session, project, [{**rows[0], "source": "assumed"}, *rows[1:]])

    await DocGeneratorService(db_session).adopt_requirement(
        project, "FR-001", recorded_by=project.user_id
    )

    current = await AgreementRepository(db_session).current(project.id)
    assert [(r.key, r.origin, r.recorded_by) for r in current] == [
        ("req:FR-001", "adopt", project.user_id)
    ]
    assert current[0].content is not None and current[0].content["source"] == "adopted"


async def test_adopt_does_not_record_in_simple_mode(db_session: AsyncSession) -> None:
    project = await create_impact_project(db_session)
    project.mode = "simple"
    rows = impact_documents()["requirements"]["rows"]
    await _new_requirements(db_session, project, [{**rows[0], "source": "assumed"}, *rows[1:]])

    await DocGeneratorService(db_session).adopt_requirement(project, "FR-001")

    assert await AgreementRepository(db_session).list_for_project(project.id) == []
    assert await AgreementService(db_session).record_adopt(
        project, RequirementRow.model_validate(rows[0]), recorded_by=None
    ) == []


async def test_pruning_keeps_versions_in_baselines(db_session: AsyncSession) -> None:
    """束が写した版(v1)は、新しい版を上限より多く作っても消えず、数にも入らない。"""
    project = await create_impact_project(db_session)
    await _create_baseline(db_session, project)
    rows = impact_documents()["requirements"]["rows"]
    for _ in range(MAX_VERSIONS_PER_DOC_TYPE + 1):
        await _new_requirements(db_session, project, rows)

    versions = await GeneratedDocumentRepository(db_session).list_versions(
        project_id=project.id, doc_type="requirements"
    )
    numbers = sorted(v.version for v in versions)
    assert numbers == [1, 3, 4, 5]
