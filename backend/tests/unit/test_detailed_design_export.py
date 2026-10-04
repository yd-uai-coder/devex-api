"""詳細設計書の zip の出力のテスト(サービス・ルート・図の描画の共通化)。

SUT: DetailedDesignExportService.bundle(app/services/detailed_design_export_service.py)、
     download_detailed_design(app/api/routes/design_stages.py)、
     DesignStageService.overview(app/services/design_stage_service.py)、
     render_diagram / unique_base(app/uml/export/files.py)、
     _render(app/services/uml_sync_service.py。共通化の後もステージ3の zip が動くこと)
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
)
from tests.fixtures.uml import create_approved_diagram, create_project_with_internal_design

from app.api.responses import content_disposition
from app.api.routes.design_stages import download_detailed_design
from app.api.routes.uml import download_bundle
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.design_stage_service import DesignStageService
from app.services.detailed_design_export_service import (
    DOCUMENT_FILENAME,
    DetailedDesignExportService,
)
from app.services.errors import DesignStagesNotAvailableError
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
    ]
    markdown = archive.read("detailed_design.md").decode()
    assert "未承認" not in markdown
    assert "![データフロー図: reservations](diagrams/dfd_reservations.svg)" in markdown
    assert "呼ばれる手順: F-01#1" in markdown
    html = archive.read("detailed_design.html").decode()
    assert "<svg" in html


async def test_bundle_marks_included_diagrams_exported(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)

    await DetailedDesignExportService(db_session).bundle(project)

    diagrams = await UmlDiagramRepository(db_session).list_for_project(project.id)
    assert {d.status for d in diagrams} == {"exported"}
    # 出力済みの図も承認済みとみなすので、段階は差し戻されない
    views, _ = await DesignStageService(db_session).overview(project)
    assert all(views[stage].state == "approved" for stage in range(1, 7))


async def test_bundle_marks_unapproved_chapters_and_omits_their_diagrams(
    db_session: AsyncSession,
) -> None:
    project = await create_stage3_project(db_session)

    bundle = await DetailedDesignExportService(db_session).bundle(project)

    archive = _open(bundle.content)
    assert not any(name.startswith("diagrams/er") for name in archive.namelist())
    markdown = archive.read("detailed_design.md").decode()
    assert "未承認(段階3が承認されていません" in markdown
    assert "未承認(段階2" not in markdown


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
    # 共通化の後も、ステージ3の zip は同じ関数で図を描く
    response = await download_bundle(db_session, project)
    assert "diagrams/component.svg" in _open(bytes(response.body)).namelist()
