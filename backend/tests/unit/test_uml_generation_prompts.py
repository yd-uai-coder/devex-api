import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from tests.fixtures.uml import INTERNAL_DESIGN_MD

from app.uml.generation import (
    GENERATION_SCHEMAS,
    ComponentGenerationOutput,
    DfdGenerationOutput,
    ErGenerationOutput,
    ExistingDataItem,
    build_generation_messages,
    build_source_text,
    extract_dfd_subjects,
)
from app.uml.validation.structural import MAX_ELEMENTS


def test_generation_schemas_cover_all_notations() -> None:
    assert {
        "component": ComponentGenerationOutput,
        "er": ErGenerationOutput,
        "dfd": DfdGenerationOutput,
    } == GENERATION_SCHEMAS


def test_layer_is_required_in_generation_schemas() -> None:
    """Phase 9の申し送り: AI生成はlayerを埋める責務を持つ(出力スキーマで必須にする)。"""
    module_schema = ComponentGenerationOutput.model_json_schema()["$defs"]["GeneratedModule"]
    process_schema = DfdGenerationOutput.model_json_schema()["$defs"]["GeneratedProcess"]

    assert "layer" in module_schema["required"]
    assert "layer" in process_schema["required"]


def test_component_source_uses_3_1_and_3_3_without_dfd_subsection() -> None:
    source = build_source_text("component", INTERNAL_DESIGN_MD)

    assert "## 3.1" in source
    assert "## 3.3" in source
    assert "## 3.2" not in source
    assert "DF-1" not in source


def test_er_source_uses_selected_tables_only_for_partial_diagram() -> None:
    whole = build_source_text("er", INTERNAL_DESIGN_MD)
    partial = build_source_text("er", INTERNAL_DESIGN_MD, er_tables=["users"])

    assert "### テーブル: reservations" in whole
    assert "### テーブル: users" in partial
    assert "reservations" not in partial


def test_dfd_source_uses_3_2_and_target_subject_only() -> None:
    subject = extract_dfd_subjects(INTERNAL_DESIGN_MD)[1]

    source = build_source_text("dfd", INTERNAL_DESIGN_MD, dfd_subject=subject)

    assert "## 3.2" in source
    assert "DF-2: GET /api/v1/reservations" in source
    assert "DF-1" not in source


def test_dfd_source_requires_subject() -> None:
    with pytest.raises(ValueError):
        build_source_text("dfd", INTERNAL_DESIGN_MD)


def test_messages_include_limit_and_existing_data_dictionary_for_dfd() -> None:
    subject = extract_dfd_subjects(INTERNAL_DESIGN_MD)[0]

    messages = build_generation_messages(
        "dfd",
        INTERNAL_DESIGN_MD,
        dfd_subject=subject,
        data_items=[ExistingDataItem(name="予約リクエスト", field_names=["item_id"])],
    )

    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert f"{MAX_ELEMENTS}個以内" in str(messages[0].content)
    assert "- 予約リクエスト(item_id)" in str(messages[1].content)
    assert "POST /api/v1/reservations" in str(messages[1].content)


def test_messages_for_component_do_not_mention_data_dictionary() -> None:
    messages = build_generation_messages("component", INTERNAL_DESIGN_MD)

    assert "データ辞書" not in str(messages[1].content)
