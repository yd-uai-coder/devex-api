"""意味モデル(`app.uml.domain`)からレイアウトを計算するエントリポイント。

`compute_layout`が、意味モデルをレイアウト内部表現(`LayoutState`)へ変換し
(`ranking`でレーン/行を算出)、`crossing_reduction.optimize`(内部で`pipeline.route`を
繰り返し呼ぶ)を実行し、結果を`uml_diagrams.layout_model`へ保存する形(`LayoutModel`)に
変換するまでの一連の流れをまとめる。ノード数上限チェック・`asyncio.to_thread`での実行は
呼び出し側(`app/services/uml_diagram_service.py`)の責務とする(HTTP境界に近い関心事のため)。
"""

from app.uml.domain import (
    ComponentSemanticModel,
    DfdDataStore,
    DfdExternalEntity,
    DfdSemanticModel,
    ErSemanticModel,
    NotationType,
    UmlElement,
)
from app.uml.layout import crossing_reduction, ranking
from app.uml.layout.model import (
    LayoutBox,
    LayoutEdge,
    LayoutEdgeGeometry,
    LayoutMetrics,
    LayoutModel,
    LayoutNode,
    LayoutState,
)
from app.uml.layout.reconcile import reconcile_layout

_AnySemanticModel = ComponentSemanticModel | ErSemanticModel | DfdSemanticModel


def _element_kind(notation: NotationType, element: UmlElement) -> str:
    """要素の種類から、レイアウト計算上のノード種別(サイズ見積もり・ポート形状に影響)を返す。"""
    if notation == "er":
        return "table"
    if notation == "dfd":
        if isinstance(element, DfdExternalEntity):
            return "ent"
        if isinstance(element, DfdDataStore):
            return "store"
        return "proc"  # DfdProcess
    return "proc"  # component


def _element_text(notation: NotationType, element: UmlElement) -> str:
    """ノードに表示するテキスト。ER図のtable種別は「1行目=テーブル名、以降=カラム」という
    `kind=="table"`ノードの入力形式(改行区切り、折り返しなし)に合わせて組み立てる。"""
    if notation == "er":
        columns = getattr(element, "columns", [])
        rows = []
        for col in columns:
            markers = "".join(
                m for m, flag in (("PK ", col.is_primary_key), ("FK ", col.is_foreign_key)) if flag
            )
            rows.append(f"{markers}{col.name}: {col.type}")
        return "\n".join([element.name, *rows])
    return element.name


def _build_state(diagram_id: str, model: _AnySemanticModel) -> LayoutState:
    lane_labels, lane_of, row_of = ranking.assign_lanes_and_rows(
        model.notation, model.elements, model.relations
    )
    nodes = {
        el.id: LayoutNode(
            id=el.id,
            lane=lane_of[el.id],
            row=row_of[el.id],
            text=_element_text(model.notation, el),
            kind=_element_kind(model.notation, el),
        )
        for el in model.elements
    }
    # devexの意味モデル(ComponentRelation/ErRelation/DfdFlow)はいずれも自由記述のラベルを
    # 持たない(DfdFlowはDataItemへの参照のみ)ため、LayoutEdge.labelは常に空文字のままになる。
    # 将来ラベルを持つnotationが追加された場合はここでelement側から引く。
    edges = [LayoutEdge(id=rel.id, a=rel.source_id, b=rel.target_id) for rel in model.relations]
    return LayoutState(diagram_id, lane_labels, nodes, edges)


def _to_layout_model(state: LayoutState) -> LayoutModel:
    nodes = {
        node_id: LayoutBox(x=n.x, y=n.y, w=n.w, h=n.h, lane=n.lane, row=n.row)
        for node_id, n in state.nodes.items()
    }
    edges = {
        e.id: LayoutEdgeGeometry(points=[(float(x), float(y)) for x, y in e.pts])
        for e in state.edges
    }
    metrics = LayoutMetrics(
        crossings=state.report["crossings"],
        overlaps=state.report["overlaps"],
        collisions=state.report["collisions"],
    )
    return LayoutModel(
        width=state.width, height=state.height, nodes=nodes, edges=edges, metrics=metrics
    )


def compute_layout(diagram_id: str, model: _AnySemanticModel) -> LayoutModel:
    """意味モデル1件のレイアウトを計算し、`uml_diagrams.layout_model`へ保存する形で返す。

    CPU負荷の高い処理(交差削減の山登り、経路探索)を含むため、呼び出し側は
    `asyncio.to_thread`でラップして呼ぶこと(このモジュール自体は同期関数のまま提供する)。
    """
    state = _build_state(diagram_id, model)
    crossing_reduction.optimize(state)
    return _to_layout_model(state)


__all__ = ["LayoutModel", "compute_layout", "reconcile_layout"]
