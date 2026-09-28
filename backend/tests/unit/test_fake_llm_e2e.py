from langchain_core.messages import HumanMessage, SystemMessage

from app.ai.llm.fake import E2eFakeLLM
from app.schemas.generation import HearingCompletionCheck
from app.services.doc_generator_service import _DOC_TYPE_PROMPTS, _SELF_DIAGNOSIS_SYSTEM_PROMPT


async def _completion(llm: E2eFakeLLM, *, human_message_count: int) -> HearingCompletionCheck:
    messages = [SystemMessage(content="dummy")]
    messages += [HumanMessage(content=f"turn {i}") for i in range(human_message_count)]
    messages.append(HumanMessage(content="completion check prompt"))  # 末尾は常に判定プロンプト自身
    structured = llm.with_structured_output(HearingCompletionCheck)
    result = await structured.ainvoke(messages)
    assert isinstance(result, HearingCompletionCheck)
    return result


async def test_insufficient_with_only_intake_message() -> None:
    result = await _completion(E2eFakeLLM(), human_message_count=1)

    assert result.is_sufficient is False


async def test_insufficient_after_one_chat_turn() -> None:
    result = await _completion(E2eFakeLLM(), human_message_count=2)

    assert result.is_sufficient is False


async def test_insufficient_after_two_chat_turns() -> None:
    result = await _completion(E2eFakeLLM(), human_message_count=3)

    assert result.is_sufficient is False


async def test_sufficient_after_three_chat_turns() -> None:
    result = await _completion(E2eFakeLLM(), human_message_count=4)

    assert result.is_sufficient is True


async def test_each_doc_type_prompt_produces_its_own_distinct_reply() -> None:
    """各doc_type専用プロンプト(実際にdoc_generator_service.pyが使うもの)を渡したとき、
    互いに異なる内容が返ることを確認する。internal_design/external_design/implementation_plan
    のプロンプトは「以下の【要件定義書】に基づき...」のように他doc_typeのラベルを本文に含むため、
    単純な部分一致だとrequirementsと誤判定されるバグがあった(実機検証で発見)。"""
    llm = E2eFakeLLM()
    replies: dict[str, str] = {}
    for doc_type, prompt in _DOC_TYPE_PROMPTS.items():
        messages = [SystemMessage(content=prompt), HumanMessage(content="dummy input")]
        result = await llm.ainvoke(messages)
        # E2eFakeLLMは常にstrを返す(型注釈上の保証は無いため明示)
        assert isinstance(result.content, str)
        replies[doc_type] = result.content

    assert len(set(replies.values())) == 4  # 4種類とも異なる内容であること
    assert "要件定義書" in replies["requirements"]
    assert "外部設計書" in replies["external_design"]
    assert "内部設計書" in replies["internal_design"]
    assert "実装計画書" in replies["implementation_plan"]


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


async def test_astream_yields_a_single_chunk_with_the_same_reply() -> None:
    llm = E2eFakeLLM()
    messages = [
        SystemMessage(content="あなたはシステム開発の要件定義を支援するAIアシスタントです。")
    ]

    chunks = [chunk.content async for chunk in llm.astream(messages)]

    assert chunks == ["[E2E Fake] 承知しました。次に、想定している主なユーザー層を教えてください。"]
