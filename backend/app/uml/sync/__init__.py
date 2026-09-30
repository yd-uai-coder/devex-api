"""承認済みのUML図を内部設計書へ反映する(M9a)ための純粋関数群。

- anchors: アンカーコメントの解析・挿入・置換(どこに置くか)
- tables: 記法ごとの要素表(何を置くか)
- staleness: 図と文書の食い違いの判定(古くなっていないか)

DBの読み書きはサービス層(app/services/uml_sync_service.py)が行う。外への依存は
app.uml.domain と app.uml.generation.sections(内部設計書の見出しの形式)だけ。
"""

from app.uml.sync.anchors import (
    APPENDIX_HEADING,
    AnchorBlock,
    ImageLink,
    find_block,
    parse_anchors,
    render_block,
    upsert_block,
    with_image_links,
)
from app.uml.sync.staleness import DocState, SyncState, diagram_sync_state
from app.uml.sync.tables import DataItemSummary, render_block_body, render_element_table

__all__ = [
    "APPENDIX_HEADING",
    "AnchorBlock",
    "DataItemSummary",
    "DocState",
    "ImageLink",
    "SyncState",
    "diagram_sync_state",
    "find_block",
    "parse_anchors",
    "render_block",
    "render_block_body",
    "render_element_table",
    "upsert_block",
    "with_image_links",
]
