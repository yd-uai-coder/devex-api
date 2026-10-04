"""承認済みのUML図を内部設計書へ反映する(M9a)ユースケース。

- 反映(13-2): 承認済みの図の要素表を、内部設計書の表示中の版にアンカーで差し込む。
  版は増やさない(D1案A、`GeneratedDocumentRepository.update_content_in_place`)。
  承認のとき(`UmlDiagramService.approve`)に1枚ずつ、再反映ボタンのときに全件を反映する。
- 埋め込み(13-3): 文書のプレビューに差し込むSVGと、図と文書の食い違い(陳腐化)を返す。
- zip(13-4): 内部設計書のmdと、反映済みの図のSVG・draw.ioをまとめて返す。

このサービスは`UmlDiagramService`をimportしない(承認が反映を呼ぶので、逆向きのimportは
循環する)。描画の規則は純粋関数(`app.uml.export`)を直接使う。
"""

import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.uml_diagram import UmlDiagram
from app.repositories.data_item import DataItemRepository
from app.repositories.generated_document import GeneratedDocumentRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.errors import DocumentNotFoundError
from app.services.uml_generation_service import SOURCE_DOC_TYPE
from app.uml.domain import (
    STATUS_AFTER_EXPORT,
    ComponentSemanticModel,
    DfdSemanticModel,
    ErSemanticModel,
    SemanticModelAdapter,
    can_export,
    parse_status,
)
from app.uml.export import (
    ExportFormat,
    diagram_title,
    export_filename,
    render_diagram,
    unique_base,
)
from app.uml.sync import (
    DataItemSummary,
    ImageLink,
    SyncState,
    diagram_sync_state,
    parse_anchors,
    render_block_body,
    render_element_table,
    upsert_block,
    with_image_links,
)

_AnyModel = ComponentSemanticModel | ErSemanticModel | DfdSemanticModel


@dataclass(frozen=True)
class DiagramEmbed:
    """文書のプレビューに差し込む図1枚分(`svg`は承認済みの図だけ)。"""

    diagram: UmlDiagram
    title: str
    sync_state: SyncState
    svg: str | None


@dataclass(frozen=True)
class BundleFile:
    """zipでまとめたダウンロード1件分(ルートがそのままレスポンスにする)。"""

    filename: str
    content: bytes
    media_type: str


BUNDLE_FILENAME = "internal_design.zip"
BUNDLE_MARKDOWN_NAME = "internal_design.md"
BUNDLE_DIAGRAM_DIR = "diagrams"


class UmlSyncService:
    def __init__(self, session: AsyncSession) -> None:
        # session: DB操作用の非同期セッション
        self._session = session
        self._documents = GeneratedDocumentRepository(session)
        self._diagrams = UmlDiagramRepository(session)
        self._data_items = DataItemRepository(session)

    async def reflect(self, diagram: UmlDiagram) -> bool:
        """承認済みの図1枚を、内部設計書の表示中の版へ反映する。反映したらTrue。

        承認(`UmlDiagramService.approve`)と同じトランザクションで呼ぶため、commitしない。
        内部設計書が無い、または図が承認済みでない場合は何もしない(承認自体は妨げない)。"""
        document = await self._documents.get_current(
            project_id=diagram.project_id, doc_type=SOURCE_DOC_TYPE
        )
        if document is None or not _is_approved(diagram):
            return False
        data_items = await self._data_item_summaries(diagram.project_id)
        content = _apply(document.content, diagram, data_items)
        if content != document.content:
            await self._documents.update_content_in_place(document, content)
        return True

    async def reflect_all(self, project_id: uuid.UUID) -> int:
        """承認済みの図すべてを反映し直す(文書の再生成・復元でアンカーが消えた場合の再反映)。
        反映した図の数を返す。内部設計書が無ければDocumentNotFoundError(404)。"""
        document = await self._documents.get_current(
            project_id=project_id, doc_type=SOURCE_DOC_TYPE
        )
        if document is None:
            raise DocumentNotFoundError(f"{SOURCE_DOC_TYPE} not found for project {project_id}")
        diagrams = [d for d in await self._diagrams.list_for_project(project_id) if _is_approved(d)]
        data_items = await self._data_item_summaries(project_id)
        content = document.content
        for diagram in diagrams:
            content = _apply(content, diagram, data_items)
        if content != document.content:
            await self._documents.update_content_in_place(document, content)
        await self._session.commit()
        return len(diagrams)

    async def list_embeds(self, project_id: uuid.UUID) -> list[DiagramEmbed]:
        """プロジェクトの全図について、文書との食い違いと、差し込むSVG(承認済みの図だけ)を返す。
        SVGは出力(`UmlDiagramService.export`)と同じ規則で描くが、状態は変えない
        (`exported`は図のファイルを出力した記録であり、プレビューで見ただけでは記録しない)。"""
        document = await self._documents.get_current(
            project_id=project_id, doc_type=SOURCE_DOC_TYPE
        )
        anchors = (
            {block.diagram_id: block for block in parse_anchors(document.content)}
            if document is not None
            else {}
        )
        names = await self._data_item_names(project_id)
        embeds: list[DiagramEmbed] = []
        for diagram in await self._diagrams.list_for_project(project_id):
            model = SemanticModelAdapter.validate_python(diagram.semantic_model)
            anchor = anchors.get(str(diagram.id))
            state = diagram_sync_state(
                status=parse_status(diagram.status),
                version=diagram.version,
                source_doc_version=(diagram.source_doc_versions or {}).get(SOURCE_DOC_TYPE),
                current_doc_version=document.version if document is not None else None,
                anchor_version=anchor.version if anchor is not None else None,
            )
            svg = _render(diagram, model, names, "svg") if _is_approved(diagram) else None
            embeds.append(
                DiagramEmbed(
                    diagram=diagram,
                    title=diagram_title(model.notation, diagram.subject),
                    sync_state=state,
                    svg=svg,
                )
            )
        return embeds

    async def bundle(self, project_id: uuid.UUID) -> BundleFile:
        """内部設計書のmdと、反映済みの図のSVG・draw.ioをzipにまとめる(D8)。

        - 入れる図: 承認済みで、表示中の版にアンカーがある図(文書に載っている図だけ)。
        - zipの中のmdは、アンカーの範囲の先頭に図ファイルへの相対リンクを入れる
          (DBの本文は変えない。mdビューアで開いても図が見えるようにするため)。
        - 入れた図は`exported`にする(図のファイルを出力した記録。
          状態が変わってもversionは増やさない)。
        """
        document = await self._documents.get_current(
            project_id=project_id, doc_type=SOURCE_DOC_TYPE
        )
        if document is None:
            raise DocumentNotFoundError(f"{SOURCE_DOC_TYPE} not found for project {project_id}")
        anchored = {block.diagram_id for block in parse_anchors(document.content)}
        names = await self._data_item_names(project_id)

        files: dict[str, str] = {}
        links: dict[str, ImageLink] = {}
        used_bases: set[str] = set()
        for diagram in await self._diagrams.list_for_project(project_id):
            if not _is_approved(diagram) or str(diagram.id) not in anchored:
                continue
            model = SemanticModelAdapter.validate_python(diagram.semantic_model)
            base = unique_base(
                export_filename(model.notation, diagram.subject, "svg").removesuffix(".svg"),
                used_bases,
            )
            svg_path = f"{BUNDLE_DIAGRAM_DIR}/{base}.svg"
            files[svg_path] = _render(diagram, model, names, "svg")
            files[f"{BUNDLE_DIAGRAM_DIR}/{base}.drawio"] = _render(diagram, model, names, "drawio")
            links[str(diagram.id)] = ImageLink(
                title=diagram_title(model.notation, diagram.subject), path=svg_path
            )
            diagram.status = STATUS_AFTER_EXPORT

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(BUNDLE_MARKDOWN_NAME, with_image_links(document.content, links))
            for path, content in files.items():
                archive.writestr(path, content)

        await self._session.flush()
        await self._session.commit()
        return BundleFile(
            filename=BUNDLE_FILENAME, content=buffer.getvalue(), media_type="application/zip"
        )

    async def _data_item_summaries(self, project_id: uuid.UUID) -> dict[uuid.UUID, DataItemSummary]:
        """DFDの要素表に載せるデータ項目(名前とフィールド名)を、プロジェクトのデータ辞書から集める。"""
        items = await self._data_items.list_for_project(project_id)
        return {
            item.id: DataItemSummary(
                name=item.name, field_names=tuple(field["name"] for field in item.fields)
            )
            for item in items
        }

    async def _data_item_names(self, project_id: uuid.UUID) -> dict[uuid.UUID, str]:
        """DFDの辺ラベル用に、データ項目id → 名前を引く。"""
        items = await self._data_items.list_for_project(project_id)
        return {item.id: item.name for item in items}


def _is_approved(diagram: UmlDiagram) -> bool:
    """反映・SVG・zipの対象か(承認済み・出力済みで、AI生成中でない)。承認済みの集合は、
    出力できる状態(`can_export`)と同じ。"""
    return diagram.generation_status != "generating" and can_export(parse_status(diagram.status))


def _apply(content: str, diagram: UmlDiagram, data_items: dict[uuid.UUID, DataItemSummary]) -> str:
    """図1枚分の要素表を、文書の本文に差し込んだ結果を返す(純粋)。"""
    model = SemanticModelAdapter.validate_python(diagram.semantic_model)
    body = render_block_body(
        diagram_title(model.notation, diagram.subject), render_element_table(model, data_items)
    )
    return upsert_block(
        content,
        diagram_id=str(diagram.id),
        version=diagram.version,
        body=body,
        notation=model.notation,
        subject=diagram.subject,
    )


def _render(
    diagram: UmlDiagram, model: _AnyModel, names: dict[uuid.UUID, str], fmt: ExportFormat
) -> str:
    """承認済みの図を、出力と同じ規則で描く(規則は`app.uml.export.render_diagram`。Phase 22)。"""
    return render_diagram(
        model,
        diagram.layout_model,
        names,
        fmt,
        diagram_id=str(diagram.id),
        title=diagram_title(model.notation, diagram.subject),
    )
