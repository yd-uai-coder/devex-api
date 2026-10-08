"""詳細設計書の zip の出力のテスト(サービス・ルート・図の描画の共通化)。

SUT: DetailedDesignExportService.bundle・bundle_procedure と missing_approvals
     (app/services/detailed_design_export_service.py)、
     download_detailed_design・download_implementation_procedure(app/api/routes/design_stages.py)、
     DesignStageService.overview(app/services/design_stage_service.py)、
     render_diagram / unique_base(app/uml/export/files.py)
ドライバ: 各テスト関数
スタブ: なし ── DB はテスト用のインメモリ SQLite(`db_session`)を使い、図はレイアウトエンジンで
実際に配置する。組み立て(md・HTML)は純粋関数のテストで確かめたので、ここでは zip の構成・
章の状態・図の状態の変化(exported)だけを見る。
"""

import io
import zipfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import (
    create_detailed_project,
    create_document_project,
    create_stage3_project,
    procedure_doc_model,
)
from tests.fixtures.uml import create_approved_diagram, create_project_with_internal_design

from app.api.responses import content_disposition
from app.api.routes.design_stages import (
    download_detailed_design,
    download_implementation_procedure,
)
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.design_stage_service import DesignStageService
from app.services.detailed_design_export_service import (
    DOCUMENT_FILENAME,
    PROCEDURE_FILENAME,
    PROCEDURE_INDEX_NAME,
    DetailedDesignExportService,
    missing_approvals,
)
from app.services.errors import DesignDocumentNotReadyError, DesignStagesNotAvailableError
from app.uml.domain import SemanticModelAdapter
from app.uml.export import render_diagram, unique_base


def _open(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


def test_unique_base_appends_suffix_for_duplicates() -> None:
    used: set[str] = set()
    assert [unique_base("dfd_a", used) for _ in range(3)] == ["dfd_a", "dfd_a_2", "dfd_a_3"]


async def test_overview_returns_states_and_approved_models(db_session: AsyncSession) -> None:
    project = await create_stage3_project(db_session)

    views, sources = await DesignStageService(db_session).overview(project)

    assert views[2].state == "approved"
    assert views[3].state == "not_started"
    assert set(sources.stages) == {1, 2}


async def test_bundle_contains_html_md_and_diagrams(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)

    bundle = await DetailedDesignExportService(db_session).bundle(project)

    assert bundle.filename == DOCUMENT_FILENAME
    archive = _open(bundle.content)
    assert sorted(archive.namelist()) == [
        "detailed_design.html",
        "detailed_design.md",
        "diagrams/component.drawio",
        "diagrams/component.svg",
        "diagrams/dfd_reservations.drawio",
        "diagrams/dfd_reservations.svg",
        "diagrams/er.drawio",
        "diagrams/er.svg",
        "implementation_plan.html",
        "implementation_plan.md",
    ]
    markdown = archive.read("detailed_design.md").decode()
    assert "未承認" not in markdown
    assert "![データフロー図: reservations](diagrams/dfd_reservations.svg)" in markdown
    assert "呼ばれる手順: F-01#1" in markdown
    html = archive.read("detailed_design.html").decode()
    assert "<svg" in html


def test_missing_approvals() -> None:
    states = {1: "approved", 2: "outdated", 3: "reviewing", 7: "approved"}

    assert missing_approvals(states, (1, 2, 3, 4, 7)) == [2, 3, 4]  # type: ignore[arg-type]
    assert missing_approvals({8: "approved"}, (8,)) == []


async def test_procedure_zip_when_stage8_is_approved(db_session: AsyncSession) -> None:
    """段階8が承認済みなら、手順書の zip に index・HTML・単位の md・AI 向けの版を入れる(直下)。"""
    project = await create_document_project(db_session)
    stages = DesignStageService(db_session)
    await stages.save(project, stage=8, expected_version=None, model=procedure_doc_model())
    await stages.approve(project, stage=8, expected_version=1)

    response = await download_implementation_procedure(db_session, project)

    assert response.headers["content-disposition"] == content_disposition(PROCEDURE_FILENAME)
    archive = _open(bytes(response.body))
    assert sorted(archive.namelist()) == [
        "M-01-T02_予約を登録する.md",
        "ai/M-01-T02.md",
        "implementation_procedure.html",
        "index.md",
    ]
    assert "## 3. 単位の一覧(依存順)" in archive.read(PROCEDURE_INDEX_NAME).decode()
    ai = archive.read("ai/M-01-T02.md").decode()
    assert "未承認" not in ai
    assert "### 段階5 F-01 予約を登録する" in ai


async def test_procedure_zip_requires_approved_stage8(db_session: AsyncSession) -> None:
    """段階8が承認済みでなければ(保存済みの手順書があっても)断る。"""
    project = await create_document_project(db_session)
    service = DetailedDesignExportService(db_session)
    with pytest.raises(DesignDocumentNotReadyError, match="段階8"):
        await service.bundle_procedure(project)

    await DesignStageService(db_session).save(
        project, stage=8, expected_version=None, model=procedure_doc_model()
    )
    with pytest.raises(DesignDocumentNotReadyError):
        await service.bundle_procedure(project)


async def test_bundle_marks_included_diagrams_exported(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)

    await DetailedDesignExportService(db_session).bundle(project)

    diagrams = await UmlDiagramRepository(db_session).list_for_project(project.id)
    assert {d.status for d in diagrams} == {"exported"}
    # 出力済みの図も承認済みとみなすので、段階は差し戻されない
    views, _ = await DesignStageService(db_session).overview(project)
    assert all(views[stage].state == "approved" for stage in range(1, 7))


async def test_bundle_requires_stages_1_to_7_and_exports_nothing(db_session: AsyncSession) -> None:
    """段階1〜7のどれかが承認済みでなければ断り、図も`exported`にしない。"""
    project = await create_stage3_project(db_session)

    with pytest.raises(DesignDocumentNotReadyError, match="段階3・4・5・6・7"):
        await DetailedDesignExportService(db_session).bundle(project)

    diagrams = await UmlDiagramRepository(db_session).list_for_project(project.id)
    assert "exported" not in {d.status for d in diagrams}


async def test_bundle_rejects_simple_mode_project(db_session: AsyncSession) -> None:
    project = await create_detailed_project(db_session, mode="simple")

    with pytest.raises(DesignStagesNotAvailableError):
        await DetailedDesignExportService(db_session).bundle(project)


async def test_route_returns_zip_attachment(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)

    response = await download_detailed_design(db_session, project)

    assert response.media_type == "application/zip"
    assert response.headers["content-disposition"] == content_disposition(DOCUMENT_FILENAME)
    assert bytes(response.body).startswith(b"PK")


async def test_render_diagram_draws_svg_of_laid_out_diagram(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)
    model = SemanticModelAdapter.validate_python(diagram.semantic_model)

    svg = render_diagram(model, diagram.layout_model, {}, "svg", diagram_id="d", title="t")

    assert svg.startswith("<svg")
