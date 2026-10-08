"""実装手順書の出力(index.md・単位ごとの md・AI 向けの版・HTML 1枚)の組み立て(純粋関数)。

段階8の手順書と承認済みの段階1〜7から、md と HTML を決定的に組み立てる。DB の読み取りと zip は
サービス(app/services/detailed_design_export_service.py・design_stage_service.py)が行う。

依存の向き: source ← markdown ← html。詳細設計書の組み立て(`app.detailed_design.document`)の
表・HTML の部品を使う。`document`の中に置かないのは、参照の展開(`procedure_doc_refs`)が
`document.markdown`を使うため(`document`の`__init__`から re-export すると循環する)。
親パッケージ(app.detailed_design)の`__init__`からは re-export しない(使うのは出力のサービスだけ)。
"""

from app.detailed_design.procedure_output.html import to_procedure_html
from app.detailed_design.procedure_output.markdown import (
    ai_warnings,
    to_ai_markdown,
    to_index_markdown,
    to_unit_markdown,
)
from app.detailed_design.procedure_output.source import (
    ProcedureOutputSource,
    UnitFinding,
    collect_findings,
    count_by_level,
    out_of_scope_lines,
    procedure_output_source,
    unit_filename,
    unit_findings,
)

__all__ = [
    "ProcedureOutputSource",
    "UnitFinding",
    "ai_warnings",
    "collect_findings",
    "count_by_level",
    "out_of_scope_lines",
    "procedure_output_source",
    "to_ai_markdown",
    "to_index_markdown",
    "to_procedure_html",
    "to_unit_markdown",
    "unit_filename",
    "unit_findings",
]
