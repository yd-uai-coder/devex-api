import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.uml.domain.base import UmlElement, UmlRelation

DfdElementType = Literal["process", "external_entity", "data_store"]


class DfdProcess(UmlElement):
    """DFDの処理ノード。`description`に「入力→出力」の加工内容を1行で持たせる
    (処理ノードには入力→出力の対応と加工の1行説明を持たせる。入出力対応そのものは
    このプロセスidを始点/終点に持つDfdFlowの集合から読み取れる)。`layer`は自動レイアウトの
    レーン割り当て用(actor等)。"""

    element_type: Literal["process"] = "process"
    description: str | None = None
    layer: str | None = None


class DfdExternalEntity(UmlElement):
    """DFDの外部実体(データの発生源・行き先)。"""

    element_type: Literal["external_entity"] = "external_entity"


class DfdDataStore(UmlElement):
    """DFDのデータストア。"""

    element_type: Literal["data_store"] = "data_store"


type DfdElement = Annotated[
    DfdProcess | DfdExternalEntity | DfdDataStore, Field(discriminator="element_type")
]


class DfdFlow(UmlRelation):
    """DFDのデータフロー。`data_item_id`でデータ辞書(DataItem)を参照する
    (自由記述ラベルを禁止し、必ずDataItemへの参照にする)。"""

    data_item_id: uuid.UUID


class DfdSemanticModel(BaseModel):
    """DFD(処理別データフロー図)の意味モデル(Single Source of Truth)。
    `relations`ではなく`flows`という名前にしたいところだが、app/uml/validation/structural.py の
    汎用構造検証(ID重複・参照切れ)をcomponent/erと同じ属性名(`elements`/`relations`)で
    共通に書けるよう、あえて`relations`のまま統一する。"""

    notation: Literal["dfd"] = "dfd"
    elements: list[DfdElement] = []
    relations: list[DfdFlow] = []
