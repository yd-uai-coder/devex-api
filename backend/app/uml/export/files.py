"""出力する図の題名・ファイル名・中身(形式ごと)を決める純粋関数。

Phase 12-4では`app/services/uml_diagram_service.py`のモジュール関数だった。Phase 13で
`app/services/uml_sync_service.py`(文書への反映・埋め込み・zip)も同じ規則を使うため、
ここへ移した(#17: 共有を選ぶ)。サービス同士をimportし合わせない ── 承認(uml_diagram_service)が
反映(uml_sync_service)を呼ぶので、逆向きのimportがあると循環するため。
"""

import re
import uuid
from collections.abc import Mapping
from typing import Any, Literal

from app.uml.domain import (
    ComponentSemanticModel,
    DfdSemanticModel,
    ErSemanticModel,
    NotationType,
)
from app.uml.export.drawio import to_drawio
from app.uml.export.render import RenderDiagram, build_render
from app.uml.export.svg import to_svg
from app.uml.layout import LayoutModel, edge_labels

ExportFormat = Literal["drawio", "svg"]

MEDIA_TYPES: dict[ExportFormat, str] = {"drawio": "application/xml", "svg": "image/svg+xml"}

# 出力するファイルの題名(devex-ui labels.tsのNOTATION_LABELS/diagramTitleと同じ文言)
_NOTATION_TITLES: dict[NotationType, str] = {
    "component": "コンポーネント図",
    "er": "ER図",
    "dfd": "データフロー図",
}

# ファイル名に使えない文字(Windowsを含む主要OSの禁止文字と制御文字)
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def diagram_title(notation: NotationType, subject: str) -> str:
    """図の題名(component・ER全体図はsubjectが空文字)。"""
    notation_title = _NOTATION_TITLES[notation]
    return f"{notation_title}: {subject}" if subject else f"{notation_title}(全体)"


def export_filename(notation: NotationType, subject: str, fmt: ExportFormat) -> str:
    """出力するファイル名(`{notation}[_{subject}].{拡張子}`)。subjectはDFDの処理名などで
    `/`を含みうる(例: `DF-1: POST /api/v1/reservations`)ため、使えない文字を`_`に置き換える。"""
    base = f"{notation}_{subject}" if subject else notation
    return f"{_UNSAFE_FILENAME_CHARS.sub('_', base).strip()}.{fmt}"


def render_content(
    diagram: RenderDiagram, fmt: ExportFormat, *, diagram_id: str, title: str
) -> str:
    """中間表現を、指定した形式の文字列に書き出す。"""
    if fmt == "drawio":
        return to_drawio(diagram, diagram_id=diagram_id, title=title)
    return to_svg(diagram)


def render_diagram(
    model: ComponentSemanticModel | ErSemanticModel | DfdSemanticModel,
    layout_model: Mapping[str, Any] | None,
    data_item_names: Mapping[uuid.UUID, str],
    fmt: ExportFormat,
    *,
    diagram_id: str,
    title: str,
) -> str:
    """承認済みの図を、出力と同じ規則で描く(承認の条件で、全要素の配置があることは確かめ済み)。

    Phase 13 では`app/services/uml_sync_service.py`の`_render`だった。Phase 22 で詳細設計書の
    出力(`app/services/detailed_design_export_service.py`)も同じ規則で図を描くため、ここへ移した
    (#17: 共有を選ぶ)。"""
    layout = LayoutModel.model_validate(layout_model)
    render = build_render(model, layout, edge_labels(model, data_item_names))
    return render_content(render, fmt, diagram_id=diagram_id, title=title)


def unique_base(base: str, used: set[str]) -> str:
    """zipの中でファイル名が重ならないようにする。禁止文字を`_`に置き換えた結果、別の図と
    同じ名前になることがあるため、2つ目以降に`_2`、`_3`…を付ける(`used`に足す)。

    Phase 13 では`app/services/uml_sync_service.py`の`_unique_base`だった(Phase 22 で移した)。"""
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate
