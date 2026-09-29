import uuid

from app.uml.domain import (
    ComponentElement,
    ComponentRelation,
    ComponentSemanticModel,
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
from app.uml.layout import compute_layout

# スタブ不要 ── 純粋関数のみで構成され、DB・外部依存を一切呼ばないため。


def test_compute_layout_for_component_model() -> None:
    model = ComponentSemanticModel(
        elements=[
            ComponentElement(id="c1", name="認証API", layer="API層"),
            ComponentElement(id="c2", name="認証サービス", layer="Service層"),
        ],
        relations=[ComponentRelation(id="r1", source_id="c1", target_id="c2")],
    )

    result = compute_layout("d1", model)

    assert set(result.nodes) == {"c1", "c2"}
    assert set(result.edges) == {"r1"}
    assert result.width > 0
    assert result.height > 0
    assert result.metrics.crossings == 0
    # layerが異なるため別レーン(lane値が異なる)に配置される
    assert result.nodes["c1"].lane != result.nodes["c2"].lane


def test_compute_layout_for_er_model_sizes_table_by_column_count() -> None:
    model = ErSemanticModel(
        elements=[
            ErElement(
                id="users",
                name="users",
                columns=[
                    ErColumn(name="id", type="uuid", is_primary_key=True),
                    ErColumn(name="email", type="varchar"),
                ],
            ),
            ErElement(
                id="orders",
                name="orders",
                columns=[ErColumn(name="id", type="uuid", is_primary_key=True)],
            ),
        ],
        relations=[
            ErRelation(id="r1", source_id="orders", target_id="users", relation_type="one_to_many")
        ],
    )

    result = compute_layout("d1", model)

    # usersは3行(テーブル名+2カラム)、ordersは2行(テーブル名+1カラム)のため、usersの方が高い
    assert result.nodes["users"].h > result.nodes["orders"].h
    # ER図は単一レーン(lane=0固定)
    assert result.nodes["users"].lane == 0
    assert result.nodes["orders"].lane == 0


def test_compute_layout_for_dfd_model_with_process_entity_store() -> None:
    data_item_id = uuid.uuid4()
    model = DfdSemanticModel(
        elements=[
            DfdExternalEntity(id="e1", name="利用者"),
            DfdProcess(id="p1", name="予約を作成する", layer="予約サービス"),
            DfdDataStore(id="s1", name="予約DB"),
        ],
        relations=[
            DfdFlow(id="f1", source_id="e1", target_id="p1", data_item_id=data_item_id),
            DfdFlow(id="f2", source_id="p1", target_id="s1", data_item_id=data_item_id),
        ],
    )

    result = compute_layout("d1", model)

    assert set(result.nodes) == {"e1", "p1", "s1"}
    assert set(result.edges) == {"f1", "f2"}
    assert result.metrics.crossings == 0
