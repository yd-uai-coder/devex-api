"""段階2(データフロー)のAIの下書きの入出力のテスト。

SUT: build_summary_messages / to_summary_drafts / build_group_dfd_messages / to_dfd_output
     (app/detailed_design/data_flow_drafting.py)、E2E用の偽LLMの段階2の出力(app/ai/llm/fake.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、LLM を呼ばないため。偽LLM(E2E)は SUT 側で、
固定の構造化出力を返すだけ。組み替えた結果は UML 図の写像(mapper.to_dfd)と DFD 規則の検証に
そのまま通す(写像と検証をそのまま使えることを確かめるため)。
"""

import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from app.ai.llm.fake import E2eFakeLLM
from app.detailed_design import FunctionRow, ProcessSummaryRow
from app.detailed_design.data_flow_drafting import (
    GeneratedGroupProcess,
    GeneratedSummary,
    GroupDfdGenerationOutput,
    ProcessSummaryGenerationOutput,
    build_group_dfd_messages,
    build_summary_messages,
    to_dfd_output,
    to_summary_drafts,
)
from app.uml.domain import DfdProcess
from app.uml.generation.mapper import required_data_items, to_dfd
from app.uml.generation.prompts import ExistingDataItem
from app.uml.generation.schemas import GeneratedFlow, GeneratedNode
from app.uml.validation.dfd_rules import validate_dfd_rules

FUNCTIONS = [
    FunctionRow(id="F-01", name="予約を登録する", trigger="POST /api/v1/x", group="予約"),
    FunctionRow(id="F-02", name="予約の一覧を返す", trigger="GET /api/v1/x", group="予約"),
]


def _to_model(output: GroupDfdGenerationOutput):
    """UML 図の写像まで通す(データ項目の UUID はサービス層の代わりにここで振る)。"""
    converted = to_dfd_output(output, FUNCTIONS)
    ids = {name: uuid.uuid4() for name in required_data_items(converted)}
    return to_dfd(converted, ids), set(ids.values())


async def test_e2e_fake_group_dfd_maps_to_valid_dfd() -> None:
    """統合スモーク: 偽LLMの DFD を組み替え → 写像 → DFD 規則の検証でエラーなし。"""
    llm = E2eFakeLLM()
    output = await llm.with_structured_output(GroupDfdGenerationOutput).ainvoke([])
    summary = await llm.with_structured_output(ProcessSummaryGenerationOutput).ainvoke([])
    assert isinstance(output, GroupDfdGenerationOutput)
    assert isinstance(summary, ProcessSummaryGenerationOutput)

    model, item_ids = _to_model(output)
    errors, _ = validate_dfd_rules(model.elements, model.relations, item_ids)

    assert errors == []
    assert [d.function_id for d in to_summary_drafts(summary)] == ["F-01", "F-02"]


def test_to_dfd_output_uses_function_ids_and_drops_foreign_processes() -> None:
    output = GroupDfdGenerationOutput(
        data_items=[],
        processes=[
            GeneratedGroupProcess(function_id=" F-01 ", description="保存", layer="受け付け"),
            GeneratedGroupProcess(function_id="F-01", description="二重", layer="受け付け"),
            GeneratedGroupProcess(function_id="F-09", description="他", layer="x"),
        ],
        external_entities=[GeneratedNode(id="e1", name="利用者")],
        data_stores=[GeneratedNode(id="s1", name="reservations")],
        flows=[
            GeneratedFlow(id="f1", source_id="e1", target_id="F-01", data_item_name="予約"),
            GeneratedFlow(id="f2", source_id="F-01", target_id="s1", data_item_name="予約"),
            GeneratedFlow(id="f3", source_id="F-09", target_id="s1", data_item_name="予約"),
        ],
    )

    model, _ = _to_model(output)
    processes = [e for e in model.elements if isinstance(e, DfdProcess)]

    assert [(p.id, p.name, p.description) for p in processes] == [
        ("F-01", "F-01 予約を登録する", "保存")
    ]
    assert [f.id for f in model.relations] == ["f1", "f2"]


def test_summary_messages_contain_functions_and_requirements() -> None:
    messages = build_summary_messages(FUNCTIONS, "# 1. 要件定義書\n予約できる")
    drafts = to_summary_drafts(
        ProcessSummaryGenerationOutput(
            rows=[GeneratedSummary(function_id="F-01", input="a", process="b", output="c")]
        )
    )

    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    content = str(messages[1].content)
    assert "F-01 予約を登録する" in content
    assert "予約できる" in content
    assert [(d.function_id, d.input, d.process, d.output) for d in drafts] == [
        ("F-01", "a", "b", "c")
    ]


def test_group_dfd_messages_contain_only_group_summaries_and_dictionary() -> None:
    summaries = [
        ProcessSummaryRow(function_id="F-01", input="予約", process="保存", output="予約"),
        ProcessSummaryRow(function_id="F-07", input="別", process="別グループ", output="別"),
    ]

    messages = build_group_dfd_messages(
        "予約",
        FUNCTIONS,
        summaries,
        "要件",
        [ExistingDataItem(name="予約", field_names=["id", "item_id"])],
    )
    content = str(messages[1].content)

    assert "## 機能グループ\n予約" in content
    assert "F-01: 入力=予約" in content
    assert "別グループ" not in content
    assert "- 予約(id, item_id)" in content
