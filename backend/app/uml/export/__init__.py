"""承認済みのUML図をdraw.io(.drawio)とSVGに書き出す(M8。決定的、AI非依存)。

`build_render`で意味モデル+配置+辺ラベルから描画用の中間表現を1回だけ組み立て、
`to_svg`/`to_drawio`が同じ中間表現からそれぞれの形式に書き出す。I/Oを持たない純粋な
パッケージで、状態の確認(承認済みか)・データ辞書の取得は呼び出し側
(`app/services/uml_diagram_service.py`)が行う。
"""

from app.uml.export.drawio import to_drawio
from app.uml.export.render import RenderDiagram, build_render, orthogonal_fallback
from app.uml.export.svg import to_svg

__all__ = ["RenderDiagram", "build_render", "orthogonal_fallback", "to_drawio", "to_svg"]
