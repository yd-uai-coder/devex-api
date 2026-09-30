"""辺(関係)に表示するラベル文字列を、意味モデルから組み立てる純粋関数。

レイアウトエンジン(`place_labels`)はラベルの文字列からその大きさを見積もり、ノードや他の
ラベルと重ならない位置を探す。出力(`app/uml/export/`)は同じ文字列をその位置に描く。
表示の文言はレビュー画面(devex-ui `reactFlowAdapter.ts`の`edgeLabelOf`)と揃える
(承認した見た目と出力の見た目を一致させるため)。

- ER: 多重度(`1:1`/`1:N`/`N:M`)
- DFD: フローが参照するデータ項目の名前(データ辞書に無ければ`(不明なデータ項目)`)
- component: ラベル無し(依存関係は矢印の向きだけで表す)
"""

import uuid
from collections.abc import Mapping

from app.uml.domain import (
    ComponentSemanticModel,
    DfdSemanticModel,
    ErRelationType,
    ErSemanticModel,
)

_AnySemanticModel = ComponentSemanticModel | ErSemanticModel | DfdSemanticModel

ER_RELATION_LABELS: dict[ErRelationType, str] = {
    "one_to_one": "1:1",
    "one_to_many": "1:N",
    "many_to_many": "N:M",
}

UNKNOWN_DATA_ITEM_LABEL = "(不明なデータ項目)"


def edge_labels(
    model: _AnySemanticModel, data_item_names: Mapping[uuid.UUID, str] | None = None
) -> dict[str, str]:
    """関係id → ラベル文字列。ラベルを持たない関係(component)は含めない。

    `data_item_names`はDFDのときだけ使う(データ項目id → 名前。呼び出し側がデータ辞書から作る)。
    """
    if isinstance(model, ErSemanticModel):
        return {rel.id: ER_RELATION_LABELS[rel.relation_type] for rel in model.relations}
    if isinstance(model, DfdSemanticModel):
        names = data_item_names or {}
        return {
            flow.id: names.get(flow.data_item_id, UNKNOWN_DATA_ITEM_LABEL)
            for flow in model.relations
        }
    return {}
