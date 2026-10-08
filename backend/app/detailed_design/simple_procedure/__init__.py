"""簡易ドキュメントモードの実装手順書の材料(純粋関数)。

簡易モードには段階1〜7が無いので、実装手順書の作業単位と参照する設計を、生成済みの4文書
(実装計画書の WBS・内部設計書)から決定的に読み取る。読み取った結果は段階7と同じ形
(`PlanModel`)にそろえ、段階8の部品(検証・生成・出力)を詳細設計モードと共有する。

親パッケージ(app.detailed_design)の`__init__`からは re-export しない(使うのは段階8の部品だけ)。
"""

from app.detailed_design.simple_procedure.internal_design import (
    DataFlow,
    SimpleDesignBook,
    module_layer,
    parse_internal_design,
)
from app.detailed_design.simple_procedure.refs import (
    expand_simple_ref,
    simple_ref_label,
    simple_unit_context,
    simple_unit_refs,
)
from app.detailed_design.simple_procedure.wbs import (
    ENVIRONMENT_SECTION,
    WBS_SECTION,
    WbsIssue,
    WbsParse,
    parse_wbs,
)

__all__ = [
    "ENVIRONMENT_SECTION",
    "WBS_SECTION",
    "DataFlow",
    "SimpleDesignBook",
    "WbsIssue",
    "WbsParse",
    "expand_simple_ref",
    "module_layer",
    "parse_internal_design",
    "parse_wbs",
    "simple_ref_label",
    "simple_unit_context",
    "simple_unit_refs",
]
