"""変更要求(CR)の選択肢(2〜3の案)の整え方・保存の形・案ごとの規模(純粋関数)。

docs/external_design.md 2.16節「説明とすり合わせ」。案はステージ6 の選択肢カードの形で、名前・
対象ごとの変更後の案・結果(手間・影響・トレードオフを書く自由記述)を持ち、CR に1つのおすすめ
(案の番号と理由)を持つ。案は開発者が書く(LLM の下書きは option_drafting.py。取り込むまで案に
ならない)。

- 案は0件(案の無い CR)か2〜3件。案は位置の番号(案1〜3)で呼ぶ。
- 案の対象は、CR の対象のうちその案で変えるもの(部分集合)と、対象ごとの種類・変更後の案。
  新しい要件を足す印と予測の起点は CR に1つで、どの案にも共通。
- 規模は案ごとに、案の対象で予測して数える(`option_scales`)。合意で選んだ案の対象が、
  その後の CR の対象になる(予測の保存・反映の指示・比較は今までどおり CR の対象を使う)。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.change_requests.prediction import predict_from_snapshots
from app.change_requests.scale import Scale, predicted_scale
from app.change_requests.targets import (
    Target,
    normalize_targets,
    targets_from_json,
    targets_to_json,
)

OPTIONS_MIN = 2
OPTIONS_MAX = 3
OPTION_NAME_MAX = 100
OPTION_OUTCOME_MAX = 2000
RECOMMEND_REASON_MAX = 1000


@dataclass(frozen=True)
class ChangeOption:
    """案1つ(名前・この案で変える対象・結果)。"""

    name: str
    targets: tuple[Target, ...]
    outcome: str


@dataclass(frozen=True)
class ChangeOptions:
    """CR の案(位置の順)と、おすすめの案の番号(1始まり。無ければ None)と理由。"""

    items: tuple[ChangeOption, ...] = ()
    recommended: int | None = None
    reason: str = ""

    @property
    def empty(self) -> bool:
        return not self.items

    def option(self, number: int) -> ChangeOption:
        """番号(1始まり)の案。範囲の外なら IndexError。"""
        if not 1 <= number <= len(self.items):
            raise IndexError(number)
        return self.items[number - 1]


def option_label(number: int) -> str:
    """案の呼び名(`案1`)。"""
    return f"案{number}"


def normalize_options(options: ChangeOptions) -> ChangeOptions:
    """案の名前・結果・理由の前後の空白を除き、各案の対象を整える(normalize_targets)。案が0件なら
    空(おすすめも持たない)。次のときは ValueError。
    - 案の数が2〜3でない
    - 名前か結果が空・名前が重なる
    - おすすめの番号が範囲の外・番号があるのに理由が空・番号が無いのに理由がある"""
    if not options.items:
        if options.recommended is not None or options.reason.strip():
            raise ValueError("案が無い CR は、おすすめを持てません")
        return ChangeOptions()
    if not OPTIONS_MIN <= len(options.items) <= OPTIONS_MAX:
        raise ValueError(f"案は{OPTIONS_MIN}〜{OPTIONS_MAX}件にしてください")
    items = []
    names: set[str] = set()
    for option in options.items:
        name = " ".join(option.name.split())
        outcome = option.outcome.strip()
        if not name or not outcome:
            raise ValueError("案には名前と結果を書いてください")
        if name in names:
            raise ValueError(f"案の名前が重なっています: {name}")
        names.add(name)
        items.append(ChangeOption(name, tuple(normalize_targets(option.targets)), outcome))
    reason = options.reason.strip()
    recommended = options.recommended
    if recommended is not None:
        if not 1 <= recommended <= len(items):
            raise ValueError(f"おすすめの案の番号が範囲の外です: {recommended}")
        if not reason:
            raise ValueError("おすすめの理由を書いてください")
    elif reason:
        raise ValueError("おすすめの案を選ばずに理由は書けません")
    return ChangeOptions(tuple(items), recommended, reason)


def option_target_problems(
    options: ChangeOptions, targets: Sequence[Target], adds_requirement: bool
) -> list[str]:
    """案の対象を CR の対象と照らした問題(無ければ空)。案の対象は CR の対象の中だけ。対象の無い
    案は、新しい要件を足す CR だけ。"""
    known = {t.requirement_id for t in targets}
    problems = []
    for number, option in enumerate(options.items, start=1):
        unknown = [t.requirement_id for t in option.targets if t.requirement_id not in known]
        if unknown:
            problems.append(
                f"{option_label(number)}の対象が CR の対象にありません: {', '.join(unknown)}"
            )
        if not option.targets and not adds_requirement:
            problems.append(f"{option_label(number)}に変える対象がありません")
    return problems


def options_to_json(options: ChangeOptions) -> dict[str, Any] | None:
    """保存の形(`{items: [{name, targets, outcome}], recommended, reason}`。案が無ければ None)。"""
    if options.empty:
        return None
    return {
        "items": [
            {"name": o.name, "targets": targets_to_json(o.targets), "outcome": o.outcome}
            for o in options.items
        ],
        "recommended": options.recommended,
        "reason": options.reason,
    }


def options_from_json(value: Mapping[str, Any] | None) -> ChangeOptions:
    """保存の形から読む(None・空は案なし)。"""
    if not value:
        return ChangeOptions()
    recommended = value.get("recommended")
    return ChangeOptions(
        items=tuple(
            ChangeOption(
                str(row.get("name", "")),
                tuple(targets_from_json(row.get("targets"))),
                str(row.get("outcome", "")),
            )
            for row in value.get("items", [])
        ),
        recommended=int(recommended) if recommended is not None else None,
        reason=str(value.get("reason") or ""),
    )


def options_from_rows(
    rows: Iterable[tuple[str, Iterable[Target], str]],
    recommended: int | None = None,
    reason: str = "",
) -> ChangeOptions:
    """(名前, 対象, 結果)の並びから案を組む(スキーマ・LLM の下書きから。整えるのは
    normalize_options)。"""
    return ChangeOptions(
        tuple(ChangeOption(name, tuple(targets), outcome) for name, targets, outcome in rows),
        recommended,
        reason,
    )


@dataclass(frozen=True)
class OptionScale:
    """案1つの規模(番号は1始まり)。"""

    number: int
    name: str
    scale: Scale
    recommended: bool = False


def option_scales(
    options: ChangeOptions,
    snapshots: Mapping[str, Mapping[str, Any] | None],
    stage_states: Mapping[int, str],
    *,
    adds_requirement: bool,
    origins: Sequence[str] = (),
) -> tuple[OptionScale, ...]:
    """案ごとに、案の対象(と CR に共通の新しい要件の起点)で予測し、規模を数える(案の順)。"""
    found = []
    for number, option in enumerate(options.items, start=1):
        prediction = predict_from_snapshots(
            snapshots, option.targets, adds_requirement=adds_requirement, origins=origins
        )
        found.append(
            OptionScale(
                number=number,
                name=option.name,
                scale=predicted_scale(option.targets, prediction, snapshots, stage_states),
                recommended=options.recommended == number,
            )
        )
    return tuple(found)
