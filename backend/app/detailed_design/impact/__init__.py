"""変更影響分析(ステージ7)の純粋関数。

- elements: 設計の要素と参照の導出(文書の model と段階1〜8 の model から)
- trace: 変更の影響のたどり方(処理を軸)と理由・再生成の候補
- diff: 変わったもの(承認時の写しと今の model の、要素の単位の差分)
- marks: 見直しの印と「確認済み」(段階に記録する形・承認を止める印)
"""

from app.detailed_design.impact.diff import (
    DiffKind,
    ElementChange,
    diff_elements,
    to_trace_changes,
)
from app.detailed_design.impact.elements import (
    DOCUMENT_STAGE,
    DesignModels,
    EdgeKind,
    Element,
    ElementKind,
    Ref,
    build_elements,
    parse_db_tables,
)
from app.detailed_design.impact.marks import (
    Mark,
    RecordedChange,
    StageImpact,
    blocking_marks,
    doc_source,
    group_by_stage,
    marks_for,
    merged_elements,
    record,
    set_reviewed,
    stage_source,
)
from app.detailed_design.impact.trace import (
    EDGE_REASON,
    REGENERATION_STAGES,
    USER_REASON,
    Change,
    ChangeKind,
    ImpactItem,
    impact_of,
    regeneration_targets,
)

__all__ = [
    "DOCUMENT_STAGE",
    "EDGE_REASON",
    "REGENERATION_STAGES",
    "USER_REASON",
    "Change",
    "ChangeKind",
    "DesignModels",
    "DiffKind",
    "EdgeKind",
    "Element",
    "ElementChange",
    "ElementKind",
    "ImpactItem",
    "Mark",
    "RecordedChange",
    "Ref",
    "StageImpact",
    "blocking_marks",
    "build_elements",
    "diff_elements",
    "doc_source",
    "group_by_stage",
    "impact_of",
    "marks_for",
    "merged_elements",
    "parse_db_tables",
    "record",
    "regeneration_targets",
    "set_reviewed",
    "stage_source",
    "to_trace_changes",
]
