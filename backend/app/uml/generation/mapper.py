"""LLMの出力スキーマ(schemas.py)を、ドメインの意味モデル(app/uml/domain)へ変換する純粋関数群。

DFDだけは、データ項目を名前からUUIDへ解決する対応表(`data_item_ids_by_name`)を外から受け取る。
対応表を作る(既存を名前で引き、無いものを作成する)のはDBに触れるサービス層の責務で、
ここはその結果を受け取って組み替えるだけにする ── 変換ロジックをスタブ無しでテストできるように。
"""

import uuid
from collections.abc import Mapping

from app.uml.domain import (
    ComponentElement,
    ComponentRelation,
    ComponentSemanticModel,
    DataItemField,
    DfdDataStore,
    DfdExternalEntity,
    DfdFlow,
    DfdProcess,
    DfdSemanticModel,
    ErColumn,
    ErElement,
    ErRelation,
    ErSemanticModel,
)
from app.uml.generation.schemas import (
    ComponentGenerationOutput,
    DfdGenerationOutput,
    ErGenerationOutput,
    GenerationOutput,
)


def to_component(output: ComponentGenerationOutput) -> ComponentSemanticModel:
    return ComponentSemanticModel(
        elements=[
            ComponentElement(id=m.id, name=m.name, description=m.description, layer=m.layer)
            for m in output.modules
        ],
        relations=[
            ComponentRelation(id=d.id, source_id=d.source_id, target_id=d.target_id)
            for d in output.dependencies
        ],
    )


def to_er(output: ErGenerationOutput) -> ErSemanticModel:
    return ErSemanticModel(
        elements=[
            ErElement(
                id=t.id,
                name=t.name,
                columns=[ErColumn(**c.model_dump()) for c in t.columns],
            )
            for t in output.tables
        ],
        relations=[
            ErRelation(
                id=r.id, source_id=r.source_id, target_id=r.target_id, relation_type=r.relation_type
            )
            for r in output.relations
        ],
    )


def required_data_items(output: DfdGenerationOutput) -> dict[str, list[DataItemField]]:
    """DFDの生成結果が必要とするデータ項目を「名前→フィールド一覧」で返す。
    `data_items`で定義された項目に加え、フローが参照しているのに定義されていない名前も
    (フィールド無しで)含める ── LLMが定義を書き漏らしても、フローを落とさずに済むように。"""
    required: dict[str, list[DataItemField]] = {
        item.name: [DataItemField(name=f.name, type=f.type or None) for f in item.fields]
        for item in output.data_items
    }
    for flow in output.flows:
        required.setdefault(flow.data_item_name, [])
    return required


def to_dfd(
    output: DfdGenerationOutput, data_item_ids_by_name: Mapping[str, uuid.UUID]
) -> DfdSemanticModel:
    """`data_item_ids_by_name`は`required_data_items`の全ての名前を含む前提(サービス層が保証する)。"""
    return DfdSemanticModel(
        elements=[
            *(
                DfdProcess(id=p.id, name=p.name, description=p.description, layer=p.layer)
                for p in output.processes
            ),
            *(DfdExternalEntity(id=e.id, name=e.name) for e in output.external_entities),
            *(DfdDataStore(id=s.id, name=s.name) for s in output.data_stores),
        ],
        relations=[
            DfdFlow(
                id=f.id,
                source_id=f.source_id,
                target_id=f.target_id,
                data_item_id=data_item_ids_by_name[f.data_item_name],
            )
            for f in output.flows
        ],
    )


def to_semantic_model(
    output: GenerationOutput,
    data_item_ids_by_name: Mapping[str, uuid.UUID] | None = None,
) -> ComponentSemanticModel | ErSemanticModel | DfdSemanticModel:
    """出力スキーマの型に応じて、対応する記法の意味モデルへ変換する。"""
    if isinstance(output, ComponentGenerationOutput):
        return to_component(output)
    if isinstance(output, ErGenerationOutput):
        return to_er(output)
    return to_dfd(output, data_item_ids_by_name or {})
