"""保存済みの配置(`LayoutModel`)を、編集後の意味モデルに合わせて突き合わせる純粋関数。

レビュー画面(M5)では、ユーザーが意味モデルの要素・関係を削除したり、ノードを手で動かしたり
する。`PUT .../diagrams/{id}`はその両方を1回の保存で受け取るため、意味モデルに存在しない
要素・関係のジオメトリが配置に残らないよう、保存の直前にここで落とす。

- 未知のidはエラーにせず黙って落とす(削除した要素の座標をFEが消し忘れても保存を止めない)。
- 意味モデルにあって配置に無い要素は補わない(自動レイアウトは明示的な再実行のときだけ走らせる。M6)。
- 折れ点を捨てた辺(`points=[]`、端点のノードを手で動かした辺。D2)は、ラベルの位置
  (`label_pos`)も捨てる。ラベルは経路に沿って置いたものなので、経路と一緒に無効になる。
- `width`/`height`は、残ったノードと辺の外接矩形が元の値を超えたときだけ広げる
  (手で右下へ動かしたノードがキャンバスからはみ出さないようにするため)。
"""

from app.uml.domain import ComponentSemanticModel, DfdSemanticModel, ErSemanticModel
from app.uml.layout.model import LayoutModel

_AnySemanticModel = ComponentSemanticModel | ErSemanticModel | DfdSemanticModel


def reconcile_layout(layout: LayoutModel, model: _AnySemanticModel) -> LayoutModel:
    """`model`に存在する要素・関係のジオメトリだけを残した`LayoutModel`を返す(引数は変更しない)。"""
    element_ids = {el.id for el in model.elements}
    relation_ids = {rel.id for rel in model.relations}
    nodes = {node_id: box for node_id, box in layout.nodes.items() if node_id in element_ids}
    edges = {
        edge_id: geo if geo.points else geo.model_copy(update={"label_pos": None})
        for edge_id, geo in layout.edges.items()
        if edge_id in relation_ids
    }

    width = max(
        [layout.width]
        + [box.x + box.w for box in nodes.values()]
        + [x for geo in edges.values() for x, _ in geo.points]
    )
    height = max(
        [layout.height]
        + [box.y + box.h for box in nodes.values()]
        + [y for geo in edges.values() for _, y in geo.points]
    )
    return layout.model_copy(
        update={"nodes": nodes, "edges": edges, "width": width, "height": height}
    )
