"""合意済みの版(束)の鮮度(純粋関数。docs/external_design.md 2.17節「束が古いときの知らせ」)。

束は作った時点の文書と段階を凍結する。その後に CR を文書へ反映すると、合意の中身は束より先へ進み、
束を基準にした比べ方(資料との違い・CR の案・規模の集計)は古い文を相手にする。また、段階を古いまま
(承認の後に上流が変わった・承認していない)束に写すと、段階から数える規模は少なく出る。そこで次の
3つを返し、どれも無ければ「新しい」とする。

- 後の束: この束より後に作った束の番号(最新の束でない)。
- 束の後に反映した CR: 合意の記録(`agreement_records`)で、この束の記録より後に、CR の反映が
  中身を書き換えた要素(番号ごとに要素のキー)。重なる CR の競合と同じ`changed_after`を使う
  (文を変えなかった反映は数えない)。束に入れた CR は束より前に反映しているので入らない。
- 古かった段階: 束を作った時点で、承認のままでなかった入っている段階(規模の集計の注記と同じ規則)。

束の記録が無い(合意の記録を始める前の束)ときは、束の後の CR を判定できないので`tracked`を
False にする(古いとは言わない)。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.change_requests.conflicts import RecordFact, base_seq, changed_after
from app.change_requests.scale import outdated_stages


@dataclass(frozen=True)
class AppliedAfter:
    """束の後に反映した CR 1つと、それが中身を書き換えた要素のキー(記録の順・重複なし)。"""

    number: int
    keys: tuple[str, ...]


@dataclass(frozen=True)
class Freshness:
    """束1つの鮮度。`newer_numbers`はこの束より後の束(番号の順)、`applied_after`は束の後に反映した
    CR(反映の順)、`outdated_stages`は束を作った時点で承認のままでなかった段階(番号の順)、
    `tracked`は束の記録があり、束の後の CR を判定できたか。"""

    baseline_number: int
    newer_numbers: tuple[int, ...]
    applied_after: tuple[AppliedAfter, ...]
    outdated_stages: tuple[int, ...]
    tracked: bool = True

    @property
    def is_latest(self) -> bool:
        return not self.newer_numbers

    @property
    def fresh(self) -> bool:
        """最新の束で、束の後に反映した CR も古かった段階も無い。"""
        return self.is_latest and not self.applied_after and not self.outdated_stages

    def excluding(self, number: int) -> "Freshness":
        """束の後に反映した CR から、番号の CR を除いた鮮度(CR 自身の反映は、その CR の基準の束を
        古くしたとは言わないため)。"""
        return replace(
            self, applied_after=tuple(a for a in self.applied_after if a.number != number)
        )


def freshness(
    records: Sequence[RecordFact],
    *,
    baseline_id: Any,
    baseline_number: int,
    baseline_numbers: Sequence[int],
    stage_states: Mapping[int, str],
) -> Freshness:
    """束1つの鮮度。`records`はプロジェクトの合意の記録(順は問わない)、`baseline_numbers`は
    プロジェクトのすべての束の番号、`stage_states`は束に入っている段階の、束を作った時点の状態。"""
    ordered = sorted(records, key=lambda r: r.seq)
    start = base_seq(ordered, baseline_id)
    applied: dict[int, list[str]] = {}
    if start is not None:
        changed = changed_after(ordered, start)
        for record in sorted((r for rs in changed.values() for r in rs), key=lambda r: r.seq):
            keys = applied.setdefault(record.change_request_number or 0, [])
            if record.key not in keys:
                keys.append(record.key)
    return Freshness(
        baseline_number=baseline_number,
        newer_numbers=tuple(sorted(n for n in baseline_numbers if n > baseline_number)),
        applied_after=tuple(AppliedAfter(n, tuple(keys)) for n, keys in applied.items()),
        outdated_stages=tuple(sorted(outdated_stages(stage_states))),
        tracked=start is not None,
    )
