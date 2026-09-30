"""UML図と内部設計書の食い違い(陳腐化)を判定する純粋関数(診断7の双方向化)。

2つの向きを別々に判定する。

1. **図が古い(`source_outdated`)**: 図を生成した内部設計書の版(`source_doc_versions`)と、
   現在表示中の版が違う。内部設計書を再生成・復元した後の図は、古い文書から作られている。
   「大きい/小さい」でなく「等しくない」で比べる ── 復元では版の番号が下がることがあるため。
2. **文書が古い(`doc_state`)**: 文書に反映した図の版(アンカーの`v=`)と、図の今の状態が違う。

| 図の状態 | アンカー | doc_state |
|---|---|---|
| approved / exported | 無い | not_reflected(文書の再生成・復元で消えた。再反映が要る) |
| approved / exported | v ≠ 図のversion | outdated(承認後に編集され、承認し直したが未反映) |
| approved / exported | v = 図のversion | reflected |
| draft / reviewing | 有る | outdated(承認後に編集中。文書には前回承認した内容が残っている) |
| draft / reviewing | 無い | not_applicable(まだ承認されていない) |

文書チェーン(要件定義 → 外部設計 → 内部設計)に沿った伝播は扱わない(M9b、Phase 13bで扱う)。
"""

from dataclasses import dataclass
from typing import Literal

from app.uml.domain import DiagramStatus, can_export

DocState = Literal["reflected", "not_reflected", "outdated", "not_applicable"]


@dataclass(frozen=True)
class SyncState:
    """図1枚分の、文書との食い違い。"""

    source_outdated: bool
    doc_state: DocState


def diagram_sync_state(
    *,
    status: DiagramStatus,
    version: int,
    source_doc_version: int | None,
    current_doc_version: int | None,
    anchor_version: int | None,
) -> SyncState:
    """図の状態・版と、文書の版・アンカーの版から、食い違いを判定する。

    - `source_doc_version`: 図を生成したときの内部設計書の版(未生成ならNone)
    - `current_doc_version`: 現在表示中の内部設計書の版(文書が無ければNone)
    - `anchor_version`: 現在の文書にある、この図のアンカーの`v=`(アンカーが無ければNone)
    """
    source_outdated = (
        source_doc_version is not None
        and current_doc_version is not None
        and source_doc_version != current_doc_version
    )
    return SyncState(
        source_outdated=source_outdated,
        doc_state=_doc_state(status, version, anchor_version),
    )


def _doc_state(status: DiagramStatus, version: int, anchor_version: int | None) -> DocState:
    # 承認済み(approved / exported)は、出力できる状態と同じ集合
    if can_export(status):
        if anchor_version is None:
            return "not_reflected"
        return "reflected" if anchor_version == version else "outdated"
    return "not_applicable" if anchor_version is None else "outdated"
