"""出力する図の題名・ファイル名・中身(形式ごと)を決める純粋関数。

Phase 12-4では`app/services/uml_diagram_service.py`のモジュール関数だった。Phase 13で
`app/services/uml_sync_service.py`(文書への反映・埋め込み・zip)も同じ規則を使うため、
ここへ移した(#17: 共有を選ぶ)。サービス同士をimportし合わせない ── 承認(uml_diagram_service)が
反映(uml_sync_service)を呼ぶので、逆向きのimportがあると循環するため。
"""

import re
from typing import Literal

from app.uml.domain import NotationType
from app.uml.export.drawio import to_drawio
from app.uml.export.render import RenderDiagram
from app.uml.export.svg import to_svg

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
