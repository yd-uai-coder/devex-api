import uuid

from app.uml.domain import (
    ComponentElement,
    ComponentRelation,
    ComponentSemanticModel,
    DfdExternalEntity,
    DfdFlow,
    DfdProcess,
    DfdSemanticModel,
    ErElement,
    ErRelation,
    ErSemanticModel,
)
from app.uml.layout import edge_labels
from app.uml.layout.labels import UNKNOWN_DATA_ITEM_LABEL

# スタブ不要 ── edge_labels は純粋関数で、データ辞書はDBから引かず引数(dict)で受け取るため。


def test_er_labels_are_multiplicities() -> None:
    model = ErSemanticModel(
        elements=[ErElement(id="t1", name="users"), ErElement(id="t2", name="reservations")],
        relations=[
            ErRelation(id="r1", source_id="t1", target_id="t2", relation_type="one_to_many"),
            ErRelation(id="r2", source_id="t2", target_id="t1", relation_type="many_to_many"),
        ],
    )

    assert edge_labels(model) == {"r1": "1:N", "r2": "N:M"}


def test_dfd_labels_are_data_item_names_with_fallback() -> None:
    known, unknown = uuid.uuid4(), uuid.uuid4()
    model = DfdSemanticModel(
        elements=[DfdExternalEntity(id="e1", name="利用者"), DfdProcess(id="p1", name="予約作成")],
        relations=[
            DfdFlow(id="f1", source_id="e1", target_id="p1", data_item_id=known),
            DfdFlow(id="f2", source_id="p1", target_id="e1", data_item_id=unknown),
        ],
    )

    labels = edge_labels(model, {known: "予約リクエスト"})

    assert labels == {"f1": "予約リクエスト", "f2": UNKNOWN_DATA_ITEM_LABEL}


def test_component_relations_have_no_labels() -> None:
    model = ComponentSemanticModel(
        elements=[ComponentElement(id="c1", name="a"), ComponentElement(id="c2", name="b")],
        relations=[ComponentRelation(id="r1", source_id="c1", target_id="c2")],
    )

    assert edge_labels(model) == {}
