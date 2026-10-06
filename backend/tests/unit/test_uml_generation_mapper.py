import uuid

import pytest
from tests.fixtures.uml import component_output, dfd_output

from app.uml.domain import DfdDataStore, DfdExternalEntity, DfdProcess
from app.uml.generation import required_data_items, to_component, to_dfd
from app.uml.generation.schemas import GeneratedFlow
from app.uml.validation import validate_diagram


def test_to_component_keeps_layer_for_lane_assignment() -> None:
    model = to_component(component_output())

    assert [(e.id, e.layer) for e in model.elements] == [("m1", "api"), ("m2", "service")]
    assert model.relations[0].source_id == "m1"
    assert validate_diagram(model).is_valid


def test_required_data_items_includes_undeclared_flow_references() -> None:
    output = dfd_output()
    output.flows.append(
        GeneratedFlow(id="f3", source_id="s1", target_id="p1", data_item_name="予約状況")
    )

    required = required_data_items(output)

    assert [f.name for f in required["予約リクエスト"]] == ["item_id", "start_at"]
    # 空文字列の型は「不明」としてNoneにする
    assert required["予約リクエスト"][1].type is None
    assert required["予約状況"] == []


def test_to_dfd_resolves_data_item_names_to_ids() -> None:
    item_id = uuid.uuid4()

    model = to_dfd(dfd_output(), {"予約リクエスト": item_id})

    kinds = [type(e) for e in model.elements]
    assert kinds == [DfdProcess, DfdExternalEntity, DfdDataStore]
    assert {flow.data_item_id for flow in model.relations} == {item_id}
    assert validate_diagram(model, data_item_ids={item_id}).is_valid


def test_to_dfd_raises_when_name_is_not_resolved() -> None:
    with pytest.raises(KeyError):
        to_dfd(dfd_output(), {})
