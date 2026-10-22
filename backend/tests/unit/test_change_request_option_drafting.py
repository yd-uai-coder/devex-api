"""変更要求(CR)の選択肢の LLM の下書きの、純粋な部分(入力・構造化出力の整え・保存の形・照合)の
テスト。

SUT: app.change_requests の build_option_draft_messages / draft_to_options / draft_to_json /
     check_option_draft と、OptionDraftOut・OptionDraftItemOut・OptionDraftTargetOut・
     OptionDraftCheck・OPTION_DRAFT_STATUS_LABEL
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため。照合の説明書は、段階1〜8 を持つ
fixture の model を束の写しの形に並べて組み立てたもの
"""

from langchain_core.messages import HumanMessage, SystemMessage
from tests.fixtures.change_impact import impact_documents, impact_stages

from app.change_requests import (
    OPTION_DRAFT_STATUS_LABEL,
    OPTIONS_MAX,
    ChangeRequestFacts,
    OptionDraftItemOut,
    OptionDraftOut,
    OptionDraftTargetOut,
    Target,
    build_explanation,
    build_option_draft_messages,
    check_option_draft,
    draft_to_json,
    draft_to_options,
    options_from_json,
    predict_from_snapshots,
)
from app.change_requests.summary import MAX_EXPLANATION_CHARS

_TARGETS = [Target("FR-001"), Target("FR-002")]


def _explanation():
    snapshots = {
        **impact_documents(),
        **{f"stage:{n}": model for n, model in impact_stages().items()},
    }
    facts = ChangeRequestFacts(
        number=1,
        title="登録の確認",
        status="draft",
        content="登録の前に確認を挟む",
        background_source="meeting",
        background_date=None,
        background_memo="",
        targets=tuple(_TARGETS),
        adds_requirement=False,
        baseline_number=1,
    )
    prediction = predict_from_snapshots(snapshots, _TARGETS, adds_requirement=False)
    return build_explanation(facts, prediction, snapshots, [], {})


def _out(
    *items: OptionDraftItemOut, recommended: int | None = 1, reason: str = "早い"
) -> OptionDraftOut:
    return OptionDraftOut(options=list(items), recommended=recommended, reason=reason)


def _item(
    name: str, *ids: str, outcome: str = "手間が小さい", kind: str = "content"
) -> OptionDraftItemOut:
    return OptionDraftItemOut(
        name=name,
        targets=[
            OptionDraftTargetOut.model_validate(
                {"requirement_id": i, "kind": kind, "proposed_text": "案の文"}
            )
            for i in ids
        ],
        outcome=outcome,
    )


def test_messages_pass_only_the_explanation() -> None:
    """入力は説明書だけ(上限で切る)。"""
    messages = build_option_draft_messages("# 変更の説明書" + "あ" * MAX_EXPLANATION_CHARS)

    assert isinstance(messages[0], SystemMessage) and "2〜3の案" in str(messages[0].content)
    assert isinstance(messages[1], HumanMessage)
    assert len(str(messages[1].content)) == MAX_EXPLANATION_CHARS
    assert OPTION_DRAFT_STATUS_LABEL == {"unconfirmed": "未確認", "adopted": "取り込み済み"}


def test_draft_to_options_clips_and_keeps_order() -> None:
    """案は上限の数まで、文の前後の空白を除く。削除の対象の案の文は捨てる。保存の形は案の形と同じ。"""
    out = _out(
        _item(" 小さく ", "FR-001"),
        _item("大きく", "FR-002", kind="delete"),
        *(_item(f"案{i}", "FR-001") for i in range(OPTIONS_MAX)),
    )

    options = draft_to_options(out)

    assert len(options.items) == OPTIONS_MAX
    assert options.items[0].name == "小さく"
    assert options.items[1].targets == (Target("FR-002", "delete"),)
    assert options_from_json(draft_to_json(options)) == options
    assert draft_to_json(draft_to_options(_out(recommended=None, reason=""))) == {
        "items": [],
        "recommended": None,
        "reason": "",
    }


def test_check_passes_a_good_draft() -> None:
    """規則に合い、対象が CR の中、文の要件 ID・数字が説明書にあれば照合を通る。"""
    options = draft_to_options(_out(_item("登録だけ", "FR-001"), _item("両方", "FR-001", "FR-002")))

    check = check_option_draft(options, _explanation(), _TARGETS, False)

    assert check.ok and check.adoptable


def test_check_reports_rule_and_target_problems() -> None:
    """案の規則に合わない・CR の対象に無い要件は、取り込めない問題として返す。"""
    one = draft_to_options(_out(_item("1つだけ", "FR-001")))
    outside = draft_to_options(_out(_item("A", "FR-001"), _item("B", "NFR-001")))

    assert check_option_draft(one, _explanation(), _TARGETS, False).problems == (
        "案は2〜3件にしてください",
    )
    check = check_option_draft(outside, _explanation(), _TARGETS, False)
    assert check.problems == ("案2の対象が CR の対象にありません: NFR-001",)
    assert not check.adoptable


def test_check_reports_unknown_ids_and_numbers_without_blocking() -> None:
    """文に出る要件 ID・数字のうち説明書に無いものは返すが、取り込みは止めない。"""
    options = draft_to_options(
        _out(
            _item("A", "FR-001", outcome="FR-009 も直す。40日かかる"),
            _item("B", "FR-002"),
        )
    )

    check = check_option_draft(options, _explanation(), _TARGETS, False)

    assert check.unknown_requirement_ids == ("FR-009",)
    assert check.unknown_numbers == ("40",)
    assert check.adoptable and not check.ok
