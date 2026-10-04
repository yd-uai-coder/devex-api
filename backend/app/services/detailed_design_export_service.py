"""詳細設計書(HTML+md+図)を zip にまとめるユースケース(Phase 22)。

docs/internal_design.md 3.3節「4. 詳細設計モード」の「詳細設計書の組み立て」。

- 入力: 段階1〜6の状態と承認済みの内容(`DesignStageService.overview`)、承認済みの章が使う図
  (段階2の DFD・段階3の ER・段階4の構成図)、データ辞書。
- 組み立て: 純粋関数(`app.detailed_design.document`)が md と HTML を作る。図の描画は
  ステージ3の zip と同じ規則(`app.uml.export.render_diagram`)。
- zip に入れた図は`exported`にする(図のファイルを出力した記録。ステージ3の zip と同じ)。

ダウンロードは、どの段階が承認済みでもいつでもできる。承認していない段階の章は「未承認」になる
(Phase 22 の決定)。
"""

import uuid
import zipfile
from io import BytesIO

from sqlalchemy.ext.asyncio import AsyncSession

from app.detailed_design.data_flow import DataFlowModel, dfd_subject
from app.detailed_design.data_model import ER_SUBJECT
from app.detailed_design.document import (
    DataItemEntry,
    RenderedDiagram,
    document_source,
    to_html,
    to_markdown,
)
from app.detailed_design.stages import StageState
from app.detailed_design.structure import STRUCTURE_SUBJECT
from app.models.project import Project
from app.models.uml_diagram import UmlDiagram
from app.repositories.data_item import DataItemRepository
from app.repositories.uml_diagram import UmlDiagramRepository
from app.services.design_stage_service import DesignStageService
from app.services.uml_sync_service import BundleFile
from app.uml.domain import (
    STATUS_AFTER_EXPORT,
    ErSemanticModel,
    NotationType,
    SemanticModelAdapter,
    can_export,
    parse_status,
)
from app.uml.export import diagram_title, export_filename, render_diagram, unique_base

DOCUMENT_FILENAME = "detailed_design.zip"
DOCUMENT_HTML_NAME = "detailed_design.html"
DOCUMENT_MARKDOWN_NAME = "detailed_design.md"
DOCUMENT_DIAGRAM_DIR = "diagrams"


class DetailedDesignExportService:
    """詳細設計書の組み立てと zip の作成を担当するサービス。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._stages = DesignStageService(session)
        self._diagrams = UmlDiagramRepository(session)
        self._data_items = DataItemRepository(session)

    async def bundle(self, project: Project) -> BundleFile:
        """詳細設計書の HTML・md と、載せた図の SVG・draw.io を zip にまとめる。
        簡易ドキュメントモードのプロジェクトは`DesignStagesNotAvailableError`(409)。"""
        views, sources = await self._stages.overview(project)
        states: dict[int, StageState] = {stage: view.state for stage, view in views.items()}
        approved = sources.stages
        items = await self._data_items.list_for_project(project.id)
        names = {item.id: item.name for item in items}

        files: dict[str, str] = {}
        used: set[str] = set()
        exported: list[UmlDiagram] = []

        def add(diagram: UmlDiagram | None) -> RenderedDiagram | None:
            """承認済みの図を描いて zip に足す(承認済みでない図・配置の無い図は載せない。図の
            承認には配置が要るので、配置の無い承認済みの図は通常は無い)。"""
            if diagram is None or not _is_approved(diagram) or diagram.layout_model is None:
                return None
            model = SemanticModelAdapter.validate_python(diagram.semantic_model)
            title = diagram_title(model.notation, diagram.subject)
            base = unique_base(
                export_filename(model.notation, diagram.subject, "svg").removesuffix(".svg"), used
            )
            svg_path = f"{DOCUMENT_DIAGRAM_DIR}/{base}.svg"
            svg = render_diagram(
                model, diagram.layout_model, names, "svg", diagram_id=str(diagram.id), title=title
            )
            files[svg_path] = svg
            files[f"{DOCUMENT_DIAGRAM_DIR}/{base}.drawio"] = render_diagram(
                model,
                diagram.layout_model,
                names,
                "drawio",
                diagram_id=str(diagram.id),
                title=title,
            )
            exported.append(diagram)
            return RenderedDiagram(title=title, path=svg_path, svg=svg)

        # 段階2: 承認済みなら、選んだ機能グループの DFD を載せる
        dfd_diagrams: dict[str, RenderedDiagram] = {}
        dfd_models: list[dict] = []
        if 2 in approved:
            for group in DataFlowModel.model_validate(approved[2]).dfd_groups:
                diagram = await self._get(project.id, "dfd", dfd_subject(group))
                rendered = add(diagram)
                if diagram is not None and rendered is not None:
                    dfd_diagrams[group] = rendered
                    dfd_models.append(diagram.semantic_model or {})

        # 段階3: 承認済みなら ER(図とテーブル定義の正本)
        er = er_diagram = None
        if 3 in approved:
            er_row = await self._get(project.id, "er", ER_SUBJECT)
            er_diagram = add(er_row)
            if er_row is not None and er_diagram is not None:
                er = ErSemanticModel.model_validate(er_row.semantic_model or {})

        # 段階4: 承認済みなら構成図
        component_diagram = None
        if 4 in approved:
            component_diagram = add(await self._get(project.id, "component", STRUCTURE_SUBJECT))

        source = document_source(
            project.title,
            states,
            approved,
            dfd_diagrams=dfd_diagrams,
            dfd_models=tuple(dfd_models),
            data_items=tuple(
                DataItemEntry(
                    id=str(item.id),
                    name=item.name,
                    fields=tuple(str(f.get("name", "")) for f in item.fields),
                )
                for item in items
            ),
            er=er,
            er_diagram=er_diagram,
            component_diagram=component_diagram,
        )

        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(DOCUMENT_HTML_NAME, to_html(source))
            archive.writestr(DOCUMENT_MARKDOWN_NAME, to_markdown(source))
            for path, content in files.items():
                archive.writestr(path, content)

        for diagram in exported:
            diagram.status = STATUS_AFTER_EXPORT
        await self._session.flush()
        await self._session.commit()
        return BundleFile(
            filename=DOCUMENT_FILENAME, content=buffer.getvalue(), media_type="application/zip"
        )

    async def _get(
        self, project_id: uuid.UUID, notation: NotationType, subject: str
    ) -> UmlDiagram | None:
        return await self._diagrams.get_by_subject(
            project_id=project_id, notation=notation, subject=subject
        )


def _is_approved(diagram: UmlDiagram) -> bool:
    """載せられる図か(承認済み・出力済みで、AI生成中でない)。ステージ3の zip と同じ条件。"""
    return diagram.generation_status != "generating" and can_export(parse_status(diagram.status))
