from typing import Literal

from pydantic import BaseModel

# UML設計図パイプラインが扱う図記法(component/er/dfdの3種。
NotationType = Literal["component", "er", "dfd"]

# 設計ビュー(uml_diagrams.view)。docs/internal_design.md 3.3節③「図↔文書対応」の
# 対応表をそのまま定数化したもの。notationからviewを一意に導出できるため、
# 呼び出し側(サービス層)にviewを個別入力させず、この対応表から自動的に決める。
NOTATION_TO_VIEW: dict[NotationType, str] = {
    "component": "structure",
    "er": "data",
    "dfd": "dataflow",
}


class UmlElement(BaseModel):
    """各notationの要素(ノード)に共通する最小限の形。`id`は同一図内で一意な文字列
    (AIが生成するローカルID。DBの主キーUUIDとは別物)。バリデーション層はこの`id`を
    手がかりに重複・参照切れを検査する(app/uml/validation/structural.py)。"""

    id: str
    name: str


class UmlRelation(BaseModel):
    """各notationの関係(エッジ)に共通する最小限の形。`source_id`/`target_id`は
    同一図内のUmlElement.idを指す。"""

    id: str
    source_id: str
    target_id: str
