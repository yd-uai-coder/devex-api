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
)
from app.uml.validation import validate_diagram
from app.uml.validation.dfd_rules import validate_dfd_rules
from app.uml.validation.structural import validate_structure

# スタブ不要 ── app.uml.validation配下は純粋関数のみで構成され、DB・外部APIいずれにも
# 依存しない(意味モデルとデータ項目idの集合を受け取り、ValidationIssueのリストを返すだけ)。


def test_validate_structure_detects_duplicate_element_id() -> None:
    elements = [ComponentElement(id="c1", name="auth"), ComponentElement(id="c1", name="billing")]

    errors, _warnings = validate_structure(elements, [])

    assert any(issue.code == "DUPLICATE_ID" for issue in errors)


def test_validate_structure_detects_dangling_reference() -> None:
    elements = [ComponentElement(id="c1", name="auth")]
    relations = [ComponentRelation(id="r1", source_id="c1", target_id="missing")]

    errors, _warnings = validate_structure(elements, relations)

    assert any(issue.code == "DANGLING_REFERENCE" for issue in errors)


def test_validate_structure_warns_when_element_count_exceeds_limit() -> None:
    elements = [ComponentElement(id=f"c{i}", name=f"module{i}") for i in range(31)]

    errors, warnings = validate_structure(elements, [])

    assert errors == []
    assert any(issue.code == "TOO_MANY_ELEMENTS" for issue in warnings)


def test_validate_structure_passes_for_well_formed_model() -> None:
    elements = [ComponentElement(id="c1", name="auth"), ComponentElement(id="c2", name="billing")]
    relations = [ComponentRelation(id="r1", source_id="c1", target_id="c2")]

    errors, warnings = validate_structure(elements, relations)

    assert errors == []
    assert warnings == []


def _dfd_fixture(data_item_id: uuid.UUID) -> tuple[list, list]:
    elements = [
        DfdExternalEntity(id="e1", name="利用者"),
        DfdProcess(id="p1", name="予約を作成する"),
        DfdDataStore(id="s1", name="予約DB"),
    ]
    flows = [
        DfdFlow(id="f1", source_id="e1", target_id="p1", data_item_id=data_item_id),
        DfdFlow(id="f2", source_id="p1", target_id="s1", data_item_id=data_item_id),
    ]
    return elements, flows


def test_validate_dfd_rules_passes_for_well_formed_flow() -> None:
    data_item_id = uuid.uuid4()
    elements, flows = _dfd_fixture(data_item_id)

    errors, warnings = validate_dfd_rules(elements, flows, {data_item_id})

    assert errors == []
    assert warnings == []


def test_validate_dfd_rules_detects_unknown_data_item() -> None:
    data_item_id = uuid.uuid4()
    elements, flows = _dfd_fixture(data_item_id)

    errors, _warnings = validate_dfd_rules(elements, flows, set())

    assert any(issue.code == "UNKNOWN_DATA_ITEM" for issue in errors)


def test_validate_dfd_rules_detects_process_missing_output() -> None:
    data_item_id = uuid.uuid4()
    elements = [
        DfdExternalEntity(id="e1", name="利用者"),
        DfdProcess(id="p1", name="予約を作成する"),
    ]
    flows = [DfdFlow(id="f1", source_id="e1", target_id="p1", data_item_id=data_item_id)]

    errors, _warnings = validate_dfd_rules(elements, flows, {data_item_id})

    assert any(issue.code == "PROCESS_MISSING_OUTPUT" for issue in errors)


def test_validate_dfd_rules_detects_direct_store_to_store_flow() -> None:
    data_item_id = uuid.uuid4()
    elements = [DfdDataStore(id="s1", name="予約DB"), DfdDataStore(id="s2", name="履歴DB")]
    flows = [DfdFlow(id="f1", source_id="s1", target_id="s2", data_item_id=data_item_id)]

    errors, _warnings = validate_dfd_rules(elements, flows, {data_item_id})

    assert any(issue.code == "INVALID_DIRECT_FLOW" for issue in errors)


def test_validate_dfd_rules_warns_about_unreferenced_data_item() -> None:
    data_item_id = uuid.uuid4()
    unreferenced_id = uuid.uuid4()
    elements, flows = _dfd_fixture(data_item_id)

    _errors, warnings = validate_dfd_rules(elements, flows, {data_item_id, unreferenced_id})

    assert any(issue.code == "UNREFERENCED_DATA_ITEM" for issue in warnings)


def test_validate_dfd_rules_does_not_warn_when_other_dfd_references_item() -> None:
    """DFDは処理ごとに1枚なので、未参照の判定はプロジェクト内の全DFDを横断する。"""
    data_item_id = uuid.uuid4()
    used_elsewhere_id = uuid.uuid4()
    elements, flows = _dfd_fixture(data_item_id)

    _errors, warnings = validate_dfd_rules(
        elements, flows, {data_item_id, used_elsewhere_id}, {used_elsewhere_id}
    )

    assert not any(issue.code == "UNREFERENCED_DATA_ITEM" for issue in warnings)


def test_validate_diagram_passes_referenced_elsewhere_to_dfd_rules() -> None:
    data_item_id = uuid.uuid4()
    used_elsewhere_id = uuid.uuid4()
    elements, flows = _dfd_fixture(data_item_id)
    model = DfdSemanticModel(elements=elements, relations=flows)

    result = validate_diagram(
        model,
        data_item_ids={data_item_id, used_elsewhere_id},
        referenced_elsewhere={used_elsewhere_id},
    )

    assert result.warnings == []


def test_validate_diagram_skips_dfd_rules_for_component_notation() -> None:
    model = ComponentSemanticModel(
        elements=[ComponentElement(id="c1", name="auth")], relations=[]
    )

    result = validate_diagram(model)

    assert result.is_valid


def test_validate_diagram_runs_dfd_rules_for_dfd_notation() -> None:
    data_item_id = uuid.uuid4()
    elements, flows = _dfd_fixture(data_item_id)
    model = DfdSemanticModel(elements=elements, relations=flows)

    result = validate_diagram(model, data_item_ids=set())

    assert not result.is_valid
    assert any(issue.code == "UNKNOWN_DATA_ITEM" for issue in result.errors)
