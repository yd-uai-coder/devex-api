from typing import Annotated

from pydantic import Field, TypeAdapter

from app.uml.domain.base import NOTATION_TO_VIEW, NotationType, UmlElement, UmlRelation
from app.uml.domain.component import ComponentElement, ComponentRelation, ComponentSemanticModel
from app.uml.domain.data_item import DataItemField
from app.uml.domain.dfd import (
    DfdDataStore,
    DfdElement,
    DfdExternalEntity,
    DfdFlow,
    DfdProcess,
    DfdSemanticModel,
)
from app.uml.domain.er import ErColumn, ErElement, ErRelation, ErRelationType, ErSemanticModel
from app.uml.domain.status import (
    DIAGRAM_STATUSES,
    STATUS_AFTER_APPROVE,
    STATUS_AFTER_EDIT,
    STATUS_AFTER_EXPORT,
    DiagramStatus,
    can_approve,
    can_export,
    parse_status,
)

# 意味モデルのdiscriminated union。`notation`フィールドの値で3notationのいずれかへ解決する。
# app/schemas/uml_diagram.py(APIスキーマ)・app/uml/validation(検証)双方がこの型をそのまま再利用する
# ── 既存コードベースのJSONB(project.intake等)は生dictだが、semantic_modelは
# ここで定義した型付きUnionをAPI境界・検証層で共通に使う(pyproject.tomlにjsonschema等の
# 別ライブラリが無いため、Pydantic v2ネイティブの機構だけで構造検証を完結させる)。
SemanticModel = Annotated[
    ComponentSemanticModel | ErSemanticModel | DfdSemanticModel, Field(discriminator="notation")
]

# DBのJSONB(生dict)から型付きSemanticModelへ復元する際に使う(app/services/uml_diagram_service.py)。
SemanticModelAdapter: TypeAdapter[
    ComponentSemanticModel | ErSemanticModel | DfdSemanticModel
] = TypeAdapter(SemanticModel)

# notation別の空の意味モデルを組み立てるためのファクトリ。
# 要素・関係が空の図を作るときに使う。
_AnySemanticModel = ComponentSemanticModel | ErSemanticModel | DfdSemanticModel
_EMPTY_MODEL_FACTORIES: dict[NotationType, type[_AnySemanticModel]] = {
    "component": ComponentSemanticModel,
    "er": ErSemanticModel,
    "dfd": DfdSemanticModel,
}


def empty_semantic_model(notation: NotationType) -> _AnySemanticModel:
    """指定notationの、要素・関係が空の意味モデルを返す。"""
    return _EMPTY_MODEL_FACTORIES[notation]()


__all__ = [
    "DIAGRAM_STATUSES",
    "NOTATION_TO_VIEW",
    "STATUS_AFTER_APPROVE",
    "STATUS_AFTER_EDIT",
    "STATUS_AFTER_EXPORT",
    "ComponentElement",
    "ComponentRelation",
    "ComponentSemanticModel",
    "DataItemField",
    "DfdDataStore",
    "DfdElement",
    "DfdExternalEntity",
    "DfdFlow",
    "DfdProcess",
    "DfdSemanticModel",
    "DiagramStatus",
    "ErColumn",
    "ErElement",
    "ErRelation",
    "ErRelationType",
    "ErSemanticModel",
    "NotationType",
    "SemanticModel",
    "SemanticModelAdapter",
    "UmlElement",
    "UmlRelation",
    "can_approve",
    "can_export",
    "empty_semantic_model",
    "parse_status",
]
