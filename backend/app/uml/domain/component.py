from typing import Literal

from pydantic import BaseModel

from app.uml.domain.base import UmlElement, UmlRelation


class ComponentElement(UmlElement):
    """コンポーネント図の要素(モジュール)。`layer`はPhase 9の自動レイアウト(レーン割り当て)が
    使う属性で、AIが構造化出力時に埋める想定(未設定でも保存・検証は通す)。"""

    kind: Literal["module"] = "module"
    description: str | None = None
    layer: str | None = None


class ComponentRelation(UmlRelation):
    """コンポーネント図の関係(モジュール間の依存)。"""

    relation_type: Literal["depends_on"] = "depends_on"


class ComponentSemanticModel(BaseModel):
    """コンポーネント図の意味モデル(Single Source of Truth)。`notation`は
    discriminated unionの判別フィールド(app/uml/domain/__init__.pyのSemanticModel参照)。"""

    notation: Literal["component"] = "component"
    elements: list[ComponentElement] = []
    relations: list[ComponentRelation] = []
