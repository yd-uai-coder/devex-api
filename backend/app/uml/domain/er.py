from typing import Literal

from pydantic import BaseModel

from app.uml.domain.base import UmlElement, UmlRelation

# ER関係の多重度。appendix/stage3-requirements-organization.md 品質指標
# 「ERのテーブル、カラム、PK/FK、多重度」が図・要素表から読み取れることを要求している。
ErRelationType = Literal["one_to_one", "one_to_many", "many_to_many"]


class ErColumn(BaseModel):
    """ER図のテーブルが持つカラム1件分の定義。"""

    name: str
    type: str
    is_primary_key: bool = False
    is_foreign_key: bool = False
    nullable: bool = True


class ErElement(UmlElement):
    """ER図の要素(テーブル)。"""

    kind: Literal["table"] = "table"
    columns: list[ErColumn] = []


class ErRelation(UmlRelation):
    """ER図の関係(テーブル間の多重度付き関連)。"""

    relation_type: ErRelationType


class ErSemanticModel(BaseModel):
    """ER図の意味モデル(Single Source of Truth)。"""

    notation: Literal["er"] = "er"
    elements: list[ErElement] = []
    relations: list[ErRelation] = []
