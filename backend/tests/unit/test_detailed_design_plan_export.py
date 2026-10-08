"""組み立ての入力の切り出し(`collect`)と、zip の実装計画のテスト。

SUT: DetailedDesignExportService.collect / bundle(app/services/detailed_design_export_service.py)、
     download_detailed_design(app/api/routes/design_stages.py。zip に実装計画が入ること)
ドライバ: 各テスト関数
スタブ: なし ── DB はテスト用のインメモリ SQLite(`db_session`)を使い、図はレイアウトエンジンで
実際に配置する(fixture の`create_stage7_project`・`create_document_project`)。md・HTML の中身は
純粋関数のテストで確かめたので、ここでは集めた入力・zip の構成・図の状態だけを見る。
"""

import io
import zipfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.detailed_design import create_document_project, create_stage7_project

from app.api.routes.design_stages import download_detailed_design
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.detailed_design_export_service import (
    PLAN_HTML_NAME,
    PLAN_MARKDOWN_NAME,
    CollectedDocument,
    DetailedDesignExportService,
)
from app.services.errors import DesignDocumentNotReadyError


async def test_smoke_bundle_contains_implementation_plan(db_session: AsyncSession) -> None:
    project = await create_document_project(db_session)

    response = await download_detailed_design(db_session, project)

    archive = zipfile.ZipFile(io.BytesIO(bytes(response.body)))
    plan = archive.read(PLAN_MARKDOWN_NAME).decode()
    assert plan.startswith("# 実装計画書: p\n")
    assert "| F-01 | 予約を登録する | M-01-T02 |" in plan
    assert "<h1>p</h1>" in archive.read(PLAN_HTML_NAME).decode()
    assert "## 07 横断事項" in archive.read("detailed_design.md").decode()


async def test_bundle_is_refused_until_stage7_is_approved(db_session: AsyncSession) -> None:
    """実装計画(段階7)が承認されるまで、詳細設計書・実装計画の zip は出さない。"""
    project = await create_stage7_project(db_session)

    with pytest.raises(DesignDocumentNotReadyError, match="段階7"):
        await DetailedDesignExportService(db_session).bundle(project)


async def test_collect_without_render_reads_models_but_draws_nothing(
    db_session: AsyncSession,
) -> None:
    project = await create_stage7_project(db_session)

    collected = await DetailedDesignExportService(db_session).collect(project, render=False)

    assert isinstance(collected, CollectedDocument)
    assert collected.files == {}
    assert collected.rendered == []
    source = collected.source
    assert source.status(6) == "approved"
    assert source.status(7) == "unapproved"
    # 図は描かないが、表の導出に使う意味モデルは読む
    assert source.er is not None
    assert len(source.dfd_models) == 1
    assert source.er_diagram is None
    assert source.dfd_diagrams == {}
    # 図は出力していないので、承認済みのまま
    diagrams = await UmlDiagramRepository(db_session).list_for_project(project.id)
    assert {d.status for d in diagrams} == {"approved"}


async def test_collect_with_render_returns_drawn_diagrams(db_session: AsyncSession) -> None:
    project = await create_stage7_project(db_session)

    collected = await DetailedDesignExportService(db_session).collect(project, render=True)

    assert len(collected.rendered) == 3
    assert sorted(collected.files) == [
        "diagrams/component.drawio",
        "diagrams/component.svg",
        "diagrams/dfd_reservations.drawio",
        "diagrams/dfd_reservations.svg",
        "diagrams/er.drawio",
        "diagrams/er.svg",
    ]
    assert collected.source.er_diagram is not None
