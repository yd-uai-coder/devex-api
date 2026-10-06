import uuid

import pytest
from pydantic import ValidationError

from app.uml.domain import (
    ComponentSemanticModel,
    DfdSemanticModel,
    ErSemanticModel,
    SemanticModelAdapter,
    empty_semantic_model,
)


def test_semantic_model_adapter_resolves_component_by_notation() -> None:
    raw = {
        "notation": "component",
        "elements": [{"id": "c1", "name": "auth"}],
        "relations": [{"id": "r1", "source_id": "c1", "target_id": "c1"}],
    }

    model = SemanticModelAdapter.validate_python(raw)

    assert isinstance(model, ComponentSemanticModel)
    assert model.elements[0].kind == "module"


def test_semantic_model_adapter_resolves_er_by_notation() -> None:
    raw = {
        "notation": "er",
        "elements": [
            {
                "id": "t1",
                "name": "users",
                "columns": [{"name": "id", "type": "uuid", "is_primary_key": True}],
            }
        ],
        "relations": [],
    }

    model = SemanticModelAdapter.validate_python(raw)

    assert isinstance(model, ErSemanticModel)
    assert model.elements[0].columns[0].is_primary_key is True


def test_semantic_model_adapter_resolves_dfd_and_discriminates_elements() -> None:
    data_item_id = uuid.uuid4()
    raw = {
        "notation": "dfd",
        "elements": [
            {"id": "p1", "name": "予約を作成する", "element_type": "process"},
            {"id": "e1", "name": "利用者", "element_type": "external_entity"},
            {"id": "s1", "name": "予約DB", "element_type": "data_store"},
        ],
        "relations": [
            {
                "id": "f1",
                "source_id": "e1",
                "target_id": "p1",
                "data_item_id": str(data_item_id),
            }
        ],
    }

    model = SemanticModelAdapter.validate_python(raw)

    assert isinstance(model, DfdSemanticModel)
    element_types = {el.element_type for el in model.elements}
    assert element_types == {"process", "external_entity", "data_store"}
    assert model.relations[0].data_item_id == data_item_id


def test_dfd_flow_rejects_free_text_label_without_data_item_id() -> None:
    """M2bの「自由記述ラベルを禁止し、必ずDataItemへの参照にする」決定どおり、
    data_item_idを持たないフローはバリデーションエラーになる。"""
    raw = {
        "notation": "dfd",
        "elements": [],
        "relations": [{"id": "f1", "source_id": "e1", "target_id": "p1", "label": "予約情報"}],
    }

    with pytest.raises(ValidationError):
        SemanticModelAdapter.validate_python(raw)


@pytest.mark.parametrize(
    ("notation", "expected_type"),
    [
        ("component", ComponentSemanticModel),
        ("er", ErSemanticModel),
        ("dfd", DfdSemanticModel),
    ],
)
def test_empty_semantic_model_returns_empty_elements_and_relations(notation, expected_type) -> None:
    model = empty_semantic_model(notation)

    assert isinstance(model, expected_type)
    assert model.elements == []
    assert model.relations == []
