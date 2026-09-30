import io
import zipfile

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tests.fixtures.uml import (
    INTERNAL_DESIGN_MD,
    TWO_MODULES,
    create_approved_diagram,
    create_project,
    create_project_with_internal_design,
)

from app.repositories.generated_document import GeneratedDocumentRepository
from app.services.errors import DocumentNotFoundError
from app.services.uml_diagram_service import UmlDiagramService
from app.services.uml_sync_service import (
    BUNDLE_FILENAME,
    BUNDLE_MARKDOWN_NAME,
    UmlSyncService,
)
from app.uml.domain import ComponentSemanticModel
from app.uml.sync import find_block, parse_anchors


async def _current_internal_design(session: AsyncSession, project_id):
    document = await GeneratedDocumentRepository(session).get_current(
        project_id=project_id, doc_type="internal_design"
    )
    assert document is not None
    return document


async def test_approve_reflects_table_into_current_version_without_new_version(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)

    diagram = await create_approved_diagram(db_session, project.id)

    document = await _current_internal_design(db_session, project.id)
    assert document.version == 1
    block = find_block(document.content, str(diagram.id))
    assert block is not None
    assert block.version == diagram.version
    assert "| 認証API | モジュール | ルーター | 認証サービス |" in block.body
    assert document.content.index("## 3.3") < block.start
    versions = await GeneratedDocumentRepository(db_session).list_versions(
        project_id=project.id, doc_type="internal_design"
    )
    assert len(versions) == 1


async def test_approve_without_internal_design_still_approves(db_session: AsyncSession) -> None:
    project = await create_project(db_session)

    diagram = await create_approved_diagram(db_session, project.id)

    assert diagram.status == "approved"


async def test_reflect_all_restores_anchors_lost_by_regeneration(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)
    # 内部設計書を再生成すると、新しい版にはアンカーが無い
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="internal_design", content=INTERNAL_DESIGN_MD
    )
    await db_session.commit()

    reflected = await UmlSyncService(db_session).reflect_all(project.id)

    document = await _current_internal_design(db_session, project.id)
    assert reflected == 1
    assert document.version == 2
    assert find_block(document.content, str(diagram.id)) is not None


async def test_reflect_all_skips_diagrams_that_are_not_approved(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)
    diagram.status = "reviewing"
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="internal_design", content=INTERNAL_DESIGN_MD
    )
    await db_session.commit()

    reflected = await UmlSyncService(db_session).reflect_all(project.id)

    document = await _current_internal_design(db_session, project.id)
    assert reflected == 0
    assert parse_anchors(document.content) == []


async def test_reflect_all_requires_internal_design(db_session: AsyncSession) -> None:
    project = await create_project(db_session)

    with pytest.raises(DocumentNotFoundError):
        await UmlSyncService(db_session).reflect_all(project.id)


async def _edit_after_approval(db_session: AsyncSession, project_id, diagram) -> None:
    """承認済みの図を保存し直す(reviewingに戻り、versionが増える)。"""
    await UmlDiagramService(db_session).update(
        project_id=project_id,
        diagram_id=diagram.id,
        expected_version=diagram.version,
        semantic_model=ComponentSemanticModel.model_validate(TWO_MODULES),
    )


async def test_reapproval_replaces_the_block_in_place(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)
    await _edit_after_approval(db_session, project.id, diagram)

    reapproved = await UmlDiagramService(db_session).approve(
        project_id=project.id, diagram_id=diagram.id, expected_version=3
    )

    document = await _current_internal_design(db_session, project.id)
    [block] = parse_anchors(document.content)
    assert block.version == reapproved.version == 3


async def test_list_embeds_returns_svg_for_reflected_diagram_without_changing_status(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)

    [embed] = await UmlSyncService(db_session).list_embeds(project.id)

    assert embed.title == "コンポーネント図(全体)"
    assert embed.sync_state.doc_state == "reflected"
    assert embed.sync_state.source_outdated is False
    assert embed.svg is not None and embed.svg.startswith("<svg")
    assert embed.diagram.status == "approved"
    assert diagram.status == "approved"


async def test_list_embeds_detects_regenerated_document_in_both_directions(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)
    diagram.source_doc_versions = {"internal_design": 1}
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="internal_design", content=INTERNAL_DESIGN_MD
    )
    await db_session.commit()

    [embed] = await UmlSyncService(db_session).list_embeds(project.id)

    assert embed.sync_state.source_outdated is True  # 図は版1から作られた(今は版2)
    assert embed.sync_state.doc_state == "not_reflected"  # 版2にはアンカーが無い


async def test_list_embeds_marks_block_outdated_while_reviewing(db_session: AsyncSession) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)
    await _edit_after_approval(db_session, project.id, diagram)

    [embed] = await UmlSyncService(db_session).list_embeds(project.id)

    assert embed.sync_state.doc_state == "outdated"
    assert embed.svg is None


async def test_bundle_zips_markdown_with_image_links_and_marks_exported(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)

    bundle = await UmlSyncService(db_session).bundle(project.id)

    assert bundle.filename == BUNDLE_FILENAME
    assert bundle.media_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert sorted(archive.namelist()) == [
            "diagrams/component.drawio",
            "diagrams/component.svg",
            BUNDLE_MARKDOWN_NAME,
        ]
        markdown = archive.read(BUNDLE_MARKDOWN_NAME).decode()
    assert "![コンポーネント図(全体)](<diagrams/component.svg>)" in markdown
    assert diagram.status == "exported"
    assert diagram.version == 2
    document = await _current_internal_design(db_session, project.id)
    assert "![" not in document.content  # DBの本文には画像リンクを入れない


async def test_bundle_leaves_out_diagrams_that_are_not_in_the_document(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    diagram = await create_approved_diagram(db_session, project.id)
    await GeneratedDocumentRepository(db_session).create_version(
        project_id=project.id, doc_type="internal_design", content=INTERNAL_DESIGN_MD
    )
    await db_session.commit()

    bundle = await UmlSyncService(db_session).bundle(project.id)

    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert archive.namelist() == [BUNDLE_MARKDOWN_NAME]
    assert diagram.status == "approved"


async def test_bundle_numbers_file_names_that_collide_after_sanitizing(
    db_session: AsyncSession,
) -> None:
    project = await create_project_with_internal_design(db_session)
    await create_approved_diagram(db_session, project.id, subject="a/b")
    await create_approved_diagram(db_session, project.id, subject="a:b")

    bundle = await UmlSyncService(db_session).bundle(project.id)

    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        svgs = sorted(n for n in archive.namelist() if n.endswith(".svg"))
    assert svgs == ["diagrams/component_a_b.svg", "diagrams/component_a_b_2.svg"]
