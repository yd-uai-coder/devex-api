"""変更影響分析(ステージ7)の純粋関数。

- elements: 設計の要素と参照の導出(文書の model と段階1〜8 の model から)
- trace: 変更の影響のたどり方(処理を軸)と理由・再生成の候補
"""

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
    "ImpactItem",
    "DesignModels",
    "EdgeKind",
    "Element",
    "ElementKind",
    "Ref",
    "build_elements",
    "impact_of",
    "parse_db_tables",
    "regeneration_targets",
]
