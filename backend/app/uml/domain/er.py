from typing import Literal

from pydantic import BaseModel

from app.uml.domain.base import UmlElement, UmlRelation

# ER関係の多重度(テーブル・カラム・PK/FK とあわせて、図・要素表から読み取れるようにする)。
ErRelationType = Literal["one_to_one", "one_to_many", "many_to_many"]


class ErColumn(BaseModel):
    """ER図のテーブルが持つカラム1件分の定義。"""

    name: str
    type: str
    is_primary_key: bool = False
    is_foreign_key: bool = False
    nullable: bool = True
    # 詳細設計モードの段階3で、テーブル定義の表に出す制約(UNIQUE・既定値・FK の削除時の動きなど)と
    # 説明。テーブル定義の正本を ER に置き、別の表に二重に持たないため
    constraints: str = ""
    description: str = ""


class ErElement(UmlElement):
    """ER図の要素(テーブル)。"""

    kind: Literal["table"] = "table"
    columns: list[ErColumn] = []
    # テーブル単位の注記(複合一意制約・テーブルの役割など)
    description: str = ""


class ErRelation(UmlRelation):
    """ER図の関係(テーブル間の多重度付き関連)。"""

    relation_type: ErRelationType


class ErSemanticModel(BaseModel):
    """ER図の意味モデル(Single Source of Truth)。"""

    notation: Literal["er"] = "er"
    elements: list[ErElement] = []
    relations: list[ErRelation] = []
