"""ヒアリングの選択肢カード(純粋関数)。

docs/internal_design.md 3.3節「選択肢カード」。AI の問いを、観点・場面で聞く問い・選択肢と
選んだ結果(コスト・手間)・おすすめで出す。どの観点をカードにしてよいかはコードが決め
(`card_targets`)、LLM の返したカードはここで確かめてから保存する(`sanitize_cards`)。

カードの選択は LLM を呼ばずにコードが記録する(`apply_card_answers` で「AI の提案を採用」)。
まとめて選んだ内容は1つの利用者の発言にし(`compose_answer_message`)、その発言を根拠にする。

規模のカードだけは、選択肢をコードが決めて出す(`scale_card`)。規模は後の必須を切り替えるので、
選ばれた選択肢を規模の値(`Scale`)にそのまま結べるようにするため。
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.hearing.points import (
    PointDef,
    PointState,
    Scale,
    is_point_key,
    open_required,
    point_def,
)

# 1回の返信で聞く問いの数(カードの上限)。複数の問いを同時に出すと、答えなかった問いを
# 取りこぼしやすいので、1回に1つずつ聞く
CARD_LIMIT = 1
MIN_OPTIONS = 2
MAX_OPTIONS = 4


@dataclass(frozen=True)
class CardOption:
    label: str
    result: str  # 選んだ結果(コスト・手間)


@dataclass(frozen=True)
class Card:
    """選択肢カード1枚。`selected`は選ばれた選択肢の番号(未回答なら None)。"""

    id: str
    point_key: str
    question: str
    options: tuple[CardOption, ...]
    recommended: int
    reason: str
    selected: int | None = None

    @property
    def label(self) -> str:
        return point_def(self.point_key).label


# 規模のカードの選択肢(順 = 選択肢の番号)。結果には、選ぶと次に何を決めることになるかを書く
SCALE_CARD_OPTIONS: tuple[tuple[Scale, CardOption], ...] = (
    (
        "personal",
        CardOption(
            "自分と数人だけで使う",
            "ログインと本人のデータの分離があれば足りる。決めることが一番少ない",
        ),
    ),
    (
        "internal",
        CardOption(
            "社内の人が使う",
            "利用者の1日の流れ・運用の担当・今の仕組みからの移行・使う端末も決める",
        ),
    ),
    (
        "public",
        CardOption(
            "誰でも登録できるサービスにする",
            "社内の場合に加えて、将来の拡張も決める。負荷や不正利用への備えが要る",
        ),
    ),
)
_SCALE_CARD_REASON = "迷ったら小さく始めるのがおすすめです。使う人が増えたら、規模を選び直せます"


class CardAnswerError(ValueError):
    """カードの回答が、選べるカード・選択肢に当たらない。"""


def card_targets(
    states: Mapping[str, PointState], scale: Scale | None, *, limit: int = CARD_LIMIT
) -> list[PointDef]:
    """次に聞く観点。規模が未決なら規模(後の必須を変えるので先に聞く)。決まっていれば、未決の
    必須を定義の順に最大`limit`件(既定は1件。1回の返信で聞く問いは1つ)。"""
    targets = open_required(states, scale)
    if any(d.key == "scale" for d in targets):
        return [point_def("scale")]
    return targets[:limit]


def scale_card(card_id: str) -> Card:
    """規模のカード(LLM に作らせず、コードが出す)。"""
    return Card(
        id=card_id,
        point_key="scale",
        question=point_def("scale").question,
        options=tuple(option for _, option in SCALE_CARD_OPTIONS),
        recommended=0,
        reason=_SCALE_CARD_REASON,
    )


def scale_of_answer(answered: Sequence[tuple[Card, int]]) -> Scale | None:
    """回答に規模のカードがあれば、選ばれた規模。"""
    for card, option in answered:
        if card.point_key == "scale" and 0 <= option < len(SCALE_CARD_OPTIONS):
            return SCALE_CARD_OPTIONS[option][0]
    return None


def sanitize_cards(
    cards: Iterable[Card], allowed_keys: Iterable[str], *, limit: int = CARD_LIMIT
) -> list[Card]:
    """LLM が返したカードのうち、使えるものだけを残す(順は保つ)。

    捨てるもの: 候補の外の観点・同じ観点の2枚目・問いが空・選択肢の数が範囲の外・
    名前の空の選択肢・おすすめの番号が範囲の外・`limit`枚を超えた分。文字列の前後の空白は落とす。
    """
    allowed = set(allowed_keys)
    seen: set[str] = set()
    kept: list[Card] = []
    for card in cards:
        options = tuple(
            CardOption(option.label.strip(), option.result.strip()) for option in card.options
        )
        question = card.question.strip()
        if (
            card.point_key not in allowed
            or card.point_key in seen
            or not question
            or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS
            or any(not option.label for option in options)
            or not 0 <= card.recommended < len(options)
        ):
            continue
        seen.add(card.point_key)
        kept.append(
            replace(card, question=question, options=options, reason=card.reason.strip())
        )
        if len(kept) >= limit:
            break
    return kept


def resolve_answers(
    cards: Sequence[Card], answers: Mapping[str, int]
) -> list[tuple[Card, int]]:
    """回答(カードの id → 選択肢の番号)を、選べるカードに当てる(カードの順に並べる)。

    選べるのは`cards`(最新の AI の発言のカード)のうち未回答のものだけ。当たらない id・回答済み・
    範囲の外の番号は`CardAnswerError`。
    """
    by_id = {card.id: card for card in cards}
    for card_id, option in answers.items():
        card = by_id.get(card_id)
        if card is None:
            raise CardAnswerError("選べないカードへの回答です")
        if card.selected is not None:
            raise CardAnswerError("回答済みのカードです")
        if not 0 <= option < len(card.options):
            raise CardAnswerError("選択肢の番号が範囲の外です")
    return [(card, answers[card.id]) for card in cards if card.id in answers]


def compose_answer_message(answered: Sequence[tuple[Card, int]], text: str) -> str:
    """選んだ内容と自由な文を、1つの利用者の発言にする(判定・返信の LLM と画面はこれを読む)。"""
    lines = [f"【{card.label}】{card.options[option].label}" for card, option in answered]
    body = text.strip()
    if lines and body:
        return "\n".join(lines) + "\n\n" + body
    return "\n".join(lines) or body


def apply_card_answers(
    states: Mapping[str, PointState], answered: Sequence[tuple[Card, int]], message_id: str
) -> dict[str, PointState]:
    """選ばれた観点を「AI の提案を採用」にする。決めたことは選んだ選択肢、根拠は回答の発言。"""
    merged = dict(states)
    for card, option in answered:
        key = point_def(card.point_key).key
        merged[key] = PointState(key, "adopted", card.options[option].label, (message_id,))
    return merged


def carry_over_cards(
    previous: Sequence[Card],
    answered: Sequence[tuple[Card, int]],
    states: Mapping[str, PointState],
    new_id: Callable[[], str],
) -> list[Card]:
    """前の AI の発言のカードのうち、今回答えず、観点がまだ未決のものを、次の返信に付け直す形で返す
    (id は振り直し、未回答のまま)。1枚だけ送ったときに、残りの設問を取りこぼさないため。"""
    answered_ids = {card.id for card, _ in answered}
    return [
        replace(card, id=new_id(), selected=None)
        for card in previous
        if card.id not in answered_ids
        and card.selected is None
        and (states[card.point_key].status if card.point_key in states else "open") == "open"
    ]


def mark_selected(cards: Sequence[Card], answered: Sequence[tuple[Card, int]]) -> list[Card]:
    """カードに選ばれた選択肢を書き込む(回答の無いカードはそのまま)。"""
    selected = {card.id: option for card, option in answered}
    return [
        replace(card, selected=selected[card.id]) if card.id in selected else card
        for card in cards
    ]


def card_as_text(card: Card) -> str:
    """カードを LLM に見せる文にする(AI の発言の後ろに足して、何を聞いたかを伝える)。"""
    lines = [f"[カード: {card.label}] {card.question}"]
    for i, option in enumerate(card.options):
        mark = "(おすすめ)" if i == card.recommended else ""
        lines.append(f"{i + 1}. {option.label}{mark} ── {option.result}")
    return "\n".join(lines)


def card_to_dict(card: Card) -> dict[str, Any]:
    """`chat_histories.cards`に保存する形。画面が観点の名前を出せるよう`label`も持つ。"""
    return {
        "id": card.id,
        "point_key": card.point_key,
        "label": card.label,
        "question": card.question,
        "options": [{"label": o.label, "result": o.result} for o in card.options],
        "recommended": card.recommended,
        "reason": card.reason,
        "selected": card.selected,
    }


def card_from_dict(data: Mapping[str, Any]) -> Card | None:
    """保存した形からカードに戻す。形が崩れている・知らない観点なら None。"""
    try:
        options = tuple(
            CardOption(str(o["label"]), str(o.get("result", ""))) for o in data["options"]
        )
        selected = data.get("selected")
        card = Card(
            id=str(data["id"]),
            point_key=str(data["point_key"]),
            question=str(data["question"]),
            options=options,
            recommended=int(data["recommended"]),
            reason=str(data.get("reason", "")),
            selected=None if selected is None else int(selected),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return card if is_point_key(card.point_key) else None


def cards_from_json(raw: Sequence[Mapping[str, Any]] | None) -> list[Card]:
    """`chat_histories.cards`の値をカードの列に戻す(壊れた要素は除く)。"""
    return [card for item in raw or () if (card := card_from_dict(item)) is not None]
