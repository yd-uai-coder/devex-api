"""ヒアリングの選択肢カード(候補の観点・LLM の出力の確かめ・回答の当て方と記録)のテスト。

SUT: app/hearing/cards.py と、パッケージの re-export(app/hearing/__init__.py)
ドライバ: 各テスト関数
スタブ不要 ── どれも純粋関数(副作用なし)で、外部依存を呼ばないため。
"""

import pytest

from app.hearing import (
    Card,
    CardAnswerError,
    CardOption,
    PointState,
    apply_card_answers,
    card_as_text,
    card_from_dict,
    card_targets,
    card_to_dict,
    cards_from_json,
    carry_over_cards,
    compose_answer_message,
    initial_states,
    mark_selected,
    resolve_answers,
    sanitize_cards,
    scale_card,
    scale_of_answer,
)


def _card(
    card_id: str = "c1",
    key: str = "availability",
    *,
    options: tuple[str, ...] = ("半日止まってよい", "1時間で戻す"),
    recommended: int = 0,
    selected: int | None = None,
) -> Card:
    return Card(
        id=card_id,
        point_key=key,
        question="夜中に止まっていたら、どのくらいで困りますか",
        options=tuple(CardOption(label, f"{label}の結果") for label in options),
        recommended=recommended,
        reason="個人の規模のため",
        selected=selected,
    )


def test_card_targets_asks_scale_alone_while_it_is_open() -> None:
    assert [d.key for d in card_targets(initial_states(), None)] == ["scale"]


def test_card_targets_lists_open_required_points_up_to_limit_after_scale() -> None:
    states = initial_states()
    states["scale"] = PointState("scale", "decided", "社内", ("m",))
    states["purpose"] = PointState("purpose", "decided", "工数", ("m",))

    # 1回の返信で聞く問いは1つ(既定)。上限を変えれば未決の必須を定義の順に並べる
    assert [d.key for d in card_targets(states, "internal")] == ["users"]
    assert [d.key for d in card_targets(states, "internal", limit=3)] == [
        "users",
        "daily_flow",
        "core",
    ]


def test_sanitize_cards_drops_broken_or_unexpected_cards_and_strips_text() -> None:
    cards = [
        _card("ok", options=(" 半日 ", "1時間")),
        _card("other", key="security"),  # 候補の外
        _card("dup"),  # 同じ観点の2枚目
        _card("one", key="performance", options=("1つだけ",)),
        _card("many", key="performance", options=("a", "b", "c", "d", "e")),
        _card("blank", key="performance", options=("a", " ")),
        _card("rec", key="performance", recommended=2),
    ]

    kept = sanitize_cards(cards, ["availability", "performance"], limit=3)

    assert [c.id for c in kept] == ["ok"]
    # 既定の上限は1枚(1回の返信で聞く問いは1つ)
    two = [_card("a"), _card("b", key="performance")]
    assert [c.id for c in sanitize_cards(two, ["availability", "performance"])] == ["a"]
    assert kept[0].options[0].label == "半日"


def test_resolve_answers_orders_by_card_and_rejects_invalid_answers() -> None:
    cards = [_card("a"), _card("b", key="security"), _card("done", key="tech", selected=0)]

    assert [(c.id, o) for c, o in resolve_answers(cards, {"b": 1, "a": 0})] == [("a", 0), ("b", 1)]
    with pytest.raises(CardAnswerError):
        resolve_answers(cards, {"x": 0})
    with pytest.raises(CardAnswerError):
        resolve_answers(cards, {"done": 0})
    with pytest.raises(CardAnswerError):
        resolve_answers(cards, {"a": 2})


def test_compose_answer_message_joins_selections_and_free_text() -> None:
    answered = [(_card("a"), 1), (_card("b", key="security", options=("本人だけ", "共有")), 0)]

    assert compose_answer_message(answered, " 補足です ") == (
        "【可用性】1時間で戻す\n【セキュリティ】本人だけ\n\n補足です"
    )
    assert compose_answer_message(answered[:1], "") == "【可用性】1時間で戻す"
    assert compose_answer_message([], "文だけ") == "文だけ"


def test_apply_card_answers_records_adopted_with_answer_message_as_evidence() -> None:
    states = apply_card_answers(initial_states(), [(_card(), 0)], "msg-1")

    assert states["availability"] == PointState(
        "availability", "adopted", "半日止まってよい", ("msg-1",)
    )
    assert states["security"].status == "open"


def test_mark_selected_writes_only_answered_cards() -> None:
    cards = [_card("a"), _card("b", key="security")]

    marked = mark_selected(cards, [(cards[1], 1)])

    assert [c.selected for c in marked] == [None, 1]


def test_card_as_text_shows_question_options_and_recommendation() -> None:
    text = card_as_text(_card(recommended=1))

    assert text.splitlines() == [
        "[カード: 可用性] 夜中に止まっていたら、どのくらいで困りますか",
        "1. 半日止まってよい ── 半日止まってよいの結果",
        "2. 1時間で戻す(おすすめ) ── 1時間で戻すの結果",
    ]


def test_card_round_trips_through_dict_and_skips_broken_items() -> None:
    card = _card(selected=1)
    data = card_to_dict(card)

    assert data["label"] == "可用性"
    assert card_from_dict(data) == card
    assert card_from_dict({**data, "point_key": "unknown"}) is None
    assert cards_from_json([data, {"id": "broken"}]) == [card]
    assert cards_from_json(None) == []


def test_scale_card_is_fixed_by_code_and_maps_answer_to_scale() -> None:
    card = scale_card("s")

    assert (card.point_key, card.recommended, len(card.options)) == ("scale", 0, 3)
    assert scale_of_answer([(card, 1)]) == "internal"
    assert scale_of_answer([(_card(), 1)]) is None
    # 規模のカードの選択も、ほかのカードと同じく「AI の提案を採用」で記録する
    assert apply_card_answers(initial_states(), [(card, 2)], "m")["scale"].status == "adopted"


def test_carry_over_cards_keeps_unanswered_cards_of_open_points_with_new_ids() -> None:
    cards = [
        _card("a"),
        _card("b", key="security"),
        _card("c", key="tech"),
        _card("d", key="core", selected=0),
    ]
    states = initial_states()
    states["tech"] = PointState("tech", "decided", "Python", ("m",))
    ids = iter(["n1", "n2"])

    carried = carry_over_cards(cards, [(cards[0], 1)], states, lambda: next(ids))

    # 答えた a・観点が決まった c・回答済みの d は付け直さない
    assert [(c.id, c.point_key, c.selected) for c in carried] == [("n1", "security", None)]

