from langchain_core.messages import HumanMessage, SystemMessage
from tests.fixtures.change_impact import impact_documents, impact_stages
from tests.fixtures.external_design import e2e_fake_external_design_md
from tests.fixtures.simple_documents import e2e_fake_simple_documents

from app.ai.llm.fake import E2eFakeLLM
from app.ai.llm.paraphrase import PARAPHRASE_MARK, PARAPHRASE_SUFFIX
from app.change_requests import (
    ChangeRequestFacts,
    OptionDraftOut,
    Target,
    build_explanation,
    build_option_draft_messages,
    build_summary_messages,
    check_option_draft,
    draft_to_options,
    predict_from_snapshots,
)
from app.detailed_design.drafting import (
    FunctionListGenerationOutput,
    build_function_list_messages,
)
from app.detailed_design.plan import unit_ids
from app.detailed_design.simple_procedure import parse_internal_design, parse_wbs
from app.documents.external_design import build_model as build_external_model
from app.documents.external_design import render_external_design
from app.documents.external_design.drafting import (
    ExternalDesignGenerationOutput,
    build_external_design_messages,
)
from app.documents.external_design.drafting import to_draft as to_external_draft
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
from app.services.doc_generator_service import _SELF_DIAGNOSIS_SYSTEM_PROMPT


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
    user_messages = {e for p in judged for e in p.evidence}
    merged = merge_judgement(initial_states(), judged, user_messages=user_messages)
    return is_sufficient(merged, result.scale)


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


async def test_simple_documents_are_structured_and_readable_by_parsers() -> None:
    """偽の内部設計書・実装計画書は構造化出力で返り、model から組み立てた md を、簡易モードの
    実装手順書の解析が指摘0件で読める(E2E の画面にも「E2E Fake」が出る)。"""
    documents = await e2e_fake_simple_documents(E2eFakeLLM())

    internal_md, _ = documents["internal_design"]
    plan_md, _ = documents["implementation_plan"]
    assert "E2E Fake" in internal_md
    assert "E2E Fake" in plan_md
    assert list(parse_internal_design(internal_md).dataflows) == ["DF-1"]
    parsed = parse_wbs(plan_md)
    assert parsed.issues == ()
    assert unit_ids(parsed.plan) == ["M-01-T01", "M-01-T02"]


async def test_self_diagnosis_prompt_produces_diagnosis_reply() -> None:
    llm = E2eFakeLLM()
    messages = [SystemMessage(content=_SELF_DIAGNOSIS_SYSTEM_PROMPT), HumanMessage(content="dummy")]

    result = await llm.ainvoke(messages)

    assert "自己診断" in result.content


async def test_change_request_summary_prompt_produces_summary_reply() -> None:
    """変更の説明書の要約(本物のプロンプト)には、要件 ID・数字の無い固定の要約を返す。"""
    reply = await E2eFakeLLM().ainvoke(build_summary_messages("# 変更の説明書 CR-1"))

    assert "[E2E Fake]" in str(reply.content) and "説明書" in str(reply.content)
    body = str(reply.content).removeprefix("[E2E Fake]")
    assert not any(ch.isdigit() for ch in body)  # 照合で印が付かない


async def test_change_request_option_draft_uses_the_targets_of_the_explanation() -> None:
    """選択肢の下書き(本物のプロンプトと説明書)には、2節の対象の要件から2つの案を返し、照合を
    通る(取り込める・説明書に無い要件 ID・数字が無い)。説明書の書式を変えたときの契約テスト。"""
    snapshots = {
        **impact_documents(),
        **{f"stage:{n}": model for n, model in impact_stages().items()},
    }
    targets = [Target("FR-001"), Target("FR-002")]
    facts = ChangeRequestFacts(
        number=1,
        title="題",
        status="draft",
        content="内容",
        background_source="meeting",
        background_date=None,
        background_memo="",
        targets=tuple(targets),
        adds_requirement=False,
        baseline_number=1,
    )
    prediction = predict_from_snapshots(snapshots, targets, adds_requirement=False)
    explanation = build_explanation(facts, prediction, snapshots, [], {})

    structured = E2eFakeLLM().with_structured_output(OptionDraftOut)
    result = await structured.ainvoke(build_option_draft_messages(explanation.markdown))

    assert isinstance(result, OptionDraftOut)
    options = draft_to_options(result)
    assert [[t.requirement_id for t in o.targets] for o in options.items] == [
        ["FR-001"],
        ["FR-001", "FR-002"],
    ]
    assert check_option_draft(options, explanation, targets, False).ok


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


async def test_external_design_output_renders_with_e2e_marker() -> None:
    """偽の外部設計書は、組み立てた md に E2E の確かめる文字と、偽の要件定義書の要件ID を持つ。"""
    md = await e2e_fake_external_design_md(E2eFakeLLM())

    assert "E2E Fake" in md
    assert "# 2. 外部設計書" in md
    assert "| SCR-001 | 予約画面 |" in md
    assert "FR-001" in md and "FR-002" in md


async def _paraphrase_flow(conversation: str) -> tuple[
    RequirementsGenerationOutput,
    RequirementsGenerationOutput,
    ExternalDesignGenerationOutput,
    ExternalDesignGenerationOutput,
    FunctionListGenerationOutput,
    FunctionListGenerationOutput,
]:
    """要件定義書 → 外部設計書 → 段階1 を、本物のプロンプトで生成し、もう一度作り直す。"""
    llm = E2eFakeLLM()
    states = {d.key: PointState(d.key, "decided", "決定") for d in POINT_DEFS}
    req_first = await _requirements(
        llm, build_requirements_messages(conversation, states, None, None)
    )
    requirements = build_model(to_draft(req_first), None, states, "personal")
    req_second = await _requirements(
        llm, build_requirements_messages(conversation, states, "personal", requirements)
    )
    requirements_md = render_requirements(requirements)
    external = llm.with_structured_output(ExternalDesignGenerationOutput)
    ext_first = await external.ainvoke(build_external_design_messages(requirements_md, None))
    assert isinstance(ext_first, ExternalDesignGenerationOutput)
    design = build_external_model(to_external_draft(ext_first), None, requirements)
    ext_second = await external.ainvoke(build_external_design_messages(requirements_md, design))
    assert isinstance(ext_second, ExternalDesignGenerationOutput)
    redesign = build_external_model(to_external_draft(ext_second), design, requirements)
    functions = llm.with_structured_output(FunctionListGenerationOutput)
    fn_first = await functions.ainvoke(
        build_function_list_messages(render_external_design(design))
    )
    fn_second = await functions.ainvoke(
        build_function_list_messages(render_external_design(redesign))
    )
    assert isinstance(fn_first, FunctionListGenerationOutput)
    assert isinstance(fn_second, FunctionListGenerationOutput)
    return req_first, req_second, ext_first, ext_second, fn_first, fn_second


async def test_marked_project_is_paraphrased_on_regeneration() -> None:
    """会話に言い換えの印があると、初回は印を下流へ写して推測の利用者と外部連携を足し、作り直すと
    利用者・画面・外部連携・処理の名前を言い換える(要件の ID は引き継ぐ)。"""
    req_first, req_second, ext_first, ext_second, fn_first, fn_second = await _paraphrase_flow(
        f"備品を予約したい {PARAPHRASE_MARK}"
    )

    assert PARAPHRASE_MARK in req_first.purpose
    assert [(u.user, u.source) for u in req_first.users] == [
        ("[E2E Fake] 社員", "user"),
        ("[E2E Fake] 総務の担当者", "assumed"),
    ]
    assert [u.user for u in req_second.users] == [
        f"[E2E Fake] 社員{PARAPHRASE_SUFFIX}",
        f"[E2E Fake] 総務の担当者{PARAPHRASE_SUFFIX}",
    ]
    assert all(r.prev_id for r in req_second.requirements)
    assert PARAPHRASE_MARK in ext_first.overview
    assert [i.service for i in ext_first.integrations] == ["[E2E Fake] 予約通知 API"]
    assert [i.service for i in ext_second.integrations] == ["[E2E Fake] 予約通知"]
    assert [s.name for s in ext_second.screens] == [f"予約画面{PARAPHRASE_SUFFIX}"]
    assert [f.name for f in fn_first.functions] == ["予約を登録する", "予約の一覧を返す"]
    assert all(f.name.endswith(PARAPHRASE_SUFFIX) for f in fn_second.functions)


async def test_unmarked_project_keeps_the_same_names() -> None:
    """印の無いプロジェクトは、作り直しても同じ名前・同じ要素を返す(既存の E2E の前提)。"""
    req_first, req_second, ext_first, ext_second, fn_first, fn_second = await _paraphrase_flow(
        "備品を予約したい"
    )

    assert [u.user for u in req_second.users] == [u.user for u in req_first.users]
    assert [u.prev_id for u in req_second.users] == ["U-001"]
    assert [u.source for u in req_first.users] == ["user"]
    assert PARAPHRASE_MARK not in req_first.purpose
    assert ext_first.integrations == [] and ext_second == ext_first
    assert fn_second == fn_first
