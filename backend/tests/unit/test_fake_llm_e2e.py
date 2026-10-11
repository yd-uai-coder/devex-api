from langchain_core.messages import HumanMessage, SystemMessage

from app.ai.llm.fake import E2eFakeLLM
from app.detailed_design.plan import unit_ids
from app.detailed_design.simple_procedure import parse_wbs
from app.documents.requirements import build_model, render_requirements
from app.documents.requirements.drafting import (
    RequirementsGenerationOutput,
    build_requirements_messages,
    to_draft,
)
from app.hearing import (
    POINT_DEFS,
    JudgedPoint,
    PointState,
    initial_states,
    is_sufficient,
    merge_judgement,
    point_def,
)
from app.models.project import Project
from app.schemas.hearing import HearingPointsJudgement, HearingReplyOut
from app.services.chat_service import _build_messages
from app.services.doc_generator_service import _DOC_TYPE_PROMPTS, _SELF_DIAGNOSIS_SYSTEM_PROMPT


async def _judge(llm: E2eFakeLLM, *, human_message_count: int) -> HearingPointsJudgement:
    messages = [SystemMessage(content="dummy")]
    messages += [HumanMessage(content=f"[#{i + 1}] turn {i}") for i in range(human_message_count)]
    messages.append(HumanMessage(content="points judge prompt"))  # 末尾は常に判定プロンプト自身
    structured = llm.with_structured_output(HearingPointsJudgement)
    result = await structured.ainvoke(messages)
    assert isinstance(result, HearingPointsJudgement)
    return result


def _sufficient(result: HearingPointsJudgement) -> bool:
    """chat_serviceと同じく、表に取り込んでからコードで完了を判定する(根拠の番号はidとみなす)。"""
    judged = [
        JudgedPoint(p.key, p.status, p.summary, tuple(str(n) for n in p.evidence))
        for p in result.points
    ]
    return is_sufficient(merge_judgement(initial_states(), judged), result.scale)


async def test_insufficient_with_only_intake_message() -> None:
    result = await _judge(E2eFakeLLM(), human_message_count=1)

    assert _sufficient(result) is False


async def test_insufficient_after_one_chat_turn() -> None:
    result = await _judge(E2eFakeLLM(), human_message_count=2)

    assert _sufficient(result) is False


async def test_insufficient_after_two_chat_turns() -> None:
    result = await _judge(E2eFakeLLM(), human_message_count=3)

    assert _sufficient(result) is False


async def test_sufficient_after_three_chat_turns() -> None:
    result = await _judge(E2eFakeLLM(), human_message_count=4)

    assert _sufficient(result) is True
    assert len(result.points) == len(POINT_DEFS)
    # 根拠は番号の付いた発言のうち最後のもの
    assert all(p.evidence == [4] for p in result.points)


async def test_each_doc_type_prompt_produces_its_own_distinct_reply() -> None:
    """各doc_type専用プロンプト(実際にdoc_generator_service.pyが使うもの)を渡したとき、
    互いに異なる内容が返ることを確認する。internal_design/external_design/implementation_plan
    のプロンプトは「以下の【要件定義書】に基づき...」のように他doc_typeのラベルを本文に含むため、
    単純な部分一致だとrequirementsと誤判定されてしまう。"""
    llm = E2eFakeLLM()
    replies: dict[str, str] = {}
    for doc_type, prompt in _DOC_TYPE_PROMPTS.items():
        messages = [SystemMessage(content=prompt), HumanMessage(content="dummy input")]
        result = await llm.ainvoke(messages)
        # E2eFakeLLMは常にstrを返す(型注釈上の保証は無いため明示)
        assert isinstance(result.content, str)
        replies[doc_type] = result.content

    # 要件定義書は構造化出力なので、文の生成のプロンプトは3種類
    assert set(replies) == {"external_design", "internal_design", "implementation_plan"}
    assert len(set(replies.values())) == 3  # 3種類とも異なる内容であること
    assert "外部設計書" in replies["external_design"]
    assert "内部設計書" in replies["internal_design"]
    assert "実装計画書" in replies["implementation_plan"]


async def test_implementation_plan_reply_is_readable_as_wbs() -> None:
    """偽の実装計画書は、簡易モードの実装手順書が指摘0件で作業単位として読める書式で返る。"""
    llm = E2eFakeLLM()
    messages = [
        SystemMessage(content=_DOC_TYPE_PROMPTS["implementation_plan"]),
        HumanMessage(content="dummy input"),
    ]

    result = await llm.ainvoke(messages)

    assert isinstance(result.content, str)
    parsed = parse_wbs(result.content)
    assert parsed.issues == ()
    assert unit_ids(parsed.plan) == ["M-01-T01", "M-01-T02"]


async def test_self_diagnosis_prompt_produces_diagnosis_reply() -> None:
    llm = E2eFakeLLM()
    messages = [SystemMessage(content=_SELF_DIAGNOSIS_SYSTEM_PROMPT), HumanMessage(content="dummy")]

    result = await llm.ainvoke(messages)

    assert "自己診断" in result.content


async def test_hearing_reply_is_returned_for_ordinary_chat_turn() -> None:
    llm = E2eFakeLLM()
    messages = [
        SystemMessage(content="あなたはシステム開発の要件定義を支援するAIアシスタントです。")
    ]

    result = await llm.ainvoke(messages)

    assert "承知しました" in result.content


async def test_hearing_reply_returns_card_for_first_card_target_of_real_prompt() -> None:
    """chat_serviceの返信のプロンプト(カードにしてよい観点の行)から、先頭の観点のカードを返す。
    プロンプトの書式を変えたときに偽物が追従しているかの契約テスト。"""
    llm = E2eFakeLLM()
    messages = _build_messages(
        [], Project(), card_targets=[point_def("availability"), point_def("security")]
    )

    result = await llm.with_structured_output(HearingReplyOut).ainvoke(messages)

    assert isinstance(result, HearingReplyOut)
    assert "承知しました" in result.message
    assert [card.point_key for card in result.cards] == ["availability"]
    assert len(result.cards[0].options) == 2


async def test_hearing_reply_has_no_card_when_scale_card_is_left_to_code() -> None:
    llm = E2eFakeLLM()
    messages = _build_messages([], Project(), code_card_label="規模")

    result = await llm.with_structured_output(HearingReplyOut).ainvoke(messages)

    assert isinstance(result, HearingReplyOut)
    assert result.cards == []


async def test_astream_yields_a_single_chunk_with_the_same_reply() -> None:
    llm = E2eFakeLLM()
    messages = [
        SystemMessage(content="あなたはシステム開発の要件定義を支援するAIアシスタントです。")
    ]

    chunks = [chunk.content async for chunk in llm.astream(messages)]

    assert chunks == ["[E2E Fake] 承知しました。次に、想定している主なユーザー層を教えてください。"]


async def test_uml_schemas_return_parsed_output_with_raw_when_include_raw() -> None:
    from app.uml.generation import ComponentGenerationOutput

    llm = E2eFakeLLM()
    for schema in (ComponentGenerationOutput,):
        result = await llm.with_structured_output(schema, include_raw=True).ainvoke([])
        assert isinstance(result["parsed"], schema)
        assert result["raw"].response_metadata["finish_reason"] == "STOP"


async def _requirements(llm: E2eFakeLLM, messages) -> RequirementsGenerationOutput:
    result = await llm.with_structured_output(RequirementsGenerationOutput).ainvoke(messages)
    assert isinstance(result, RequirementsGenerationOutput)
    return result


async def test_requirements_output_keeps_ids_from_real_prompt() -> None:
    """要件定義書の偽物は、本物のプロンプトの【前の版の要件】から ID を引き継ぐ(プロンプトの
    書式を変えたときに偽物が追従しているかの契約テスト)。推測の要件を1つ含む。"""
    llm = E2eFakeLLM()
    states = {d.key: PointState(d.key, "decided", "決定") for d in POINT_DEFS}
    first = build_model(
        to_draft(await _requirements(llm, build_requirements_messages("会話", states, None, None))),
        None,
        states,
        "personal",
    )
    second_output = await _requirements(
        llm, build_requirements_messages("会話", states, "personal", first)
    )

    assert [r.prev_id for r in second_output.requirements] == [r.id for r in first.rows]
    assert [r.source for r in first.rows].count("assumed") == 1
    assert "E2E Fake" in render_requirements(first)
