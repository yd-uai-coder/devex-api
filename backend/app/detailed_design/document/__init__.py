"""詳細設計書(HTML・Markdown)の組み立て(純粋関数)。

承認済みの段階の意味モデルと図(`DocumentSource`)から、md と HTML を決定的に組み立てる。
DB の読み取り・図の描画・zip はサービス(app/services/detailed_design_export_service.py)が行う。

依存の向き: source ← views ← markdown / html(markdown と html は互いに import しない)。
親パッケージ(app.detailed_design)の`__init__`からは re-export しない ── 組み立てを使うのは
出力のサービスだけで、段階の検証・生成からは使わないため。
"""

from app.detailed_design.document.html import to_html, to_plan_html
from app.detailed_design.document.markdown import to_markdown, to_plan_markdown
from app.detailed_design.document.source import (
    CHAPTERS,
    Chapter,
    ChapterStatus,
    DataItemEntry,
    DocumentSource,
    RenderedDiagram,
    chapter_status,
    chapter_statuses,
    document_source,
)
from app.detailed_design.document.views import (
    CrudMark,
    CrudMatrix,
    FunctionPlan,
    Involvement,
    LogicView,
    StepView,
    anchor,
    crud_matrix,
    data_item_usage,
    function_plans,
    involvement,
    linked_logic_ids,
    logic_ids,
    logic_views,
    procedure_steps,
)

__all__ = [
    "CHAPTERS",
    "Chapter",
    "ChapterStatus",
    "CrudMark",
    "CrudMatrix",
    "DataItemEntry",
    "DocumentSource",
    "FunctionPlan",
    "Involvement",
    "LogicView",
    "RenderedDiagram",
    "StepView",
    "anchor",
    "chapter_status",
    "chapter_statuses",
    "crud_matrix",
    "data_item_usage",
    "document_source",
    "function_plans",
    "involvement",
    "linked_logic_ids",
    "logic_ids",
    "logic_views",
    "procedure_steps",
    "to_html",
    "to_markdown",
    "to_plan_html",
    "to_plan_markdown",
]
