"""重なる CR の競合(純粋関数。docs/external_design.md 2.14節「競合」)。

CR の案は、基準の束の文をもとに書く。束の後にほかの CR の反映で対象の要件の文が変わっていると、
この CR の案で反映したときに、先の CR の変更を上書きしうる。そこで、合意の記録(要素ごとの
追記だけの記録。`agreement_records`)から、基準の束の記録より後に、ほかの CR が対象の要件を書き
換えた行を引き、「CR-n が先に変えた(束の文 → 今の文)」として示す。

- 判定は合意の記録だけで行う(版は巡回で消えうるが、記録は消えない)。中身が前の記録と同じ行
  (文を変えなかった反映・やり直し)は、変更として数えない。
- 確かめの印(`conflict_checks`: `[{key, seq}]`)は、確かめた時点の最新の記録の`seq`を持つ。その後に
  ほかの CR がさらに変えたら、印は効かなくなる(求め直す)。
- 束の後に CR が変えた要素の取り出し(`changed_after`)は、束の鮮度の判定でも使う。
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecordFact:
    """合意の記録1行の材料。`change_request_number`は出どころが CR のときの番号(それ以外は None)、
    `content`は合意した中身(None は「外れた」)。"""

    seq: int
    key: str
    origin: str
    baseline_id: Any
    change_request_number: int | None
    content: Mapping[str, Any] | None


@dataclass(frozen=True)
class Conflict:
    """対象の要件1つの競合。`by_numbers`は束の後にこの要件を変えたほかの CR(変えた順・重複なし)、
    `base_text`は基準の束の文(束に無ければ空)、`current_text`は今の合意の文(外れていれば None)、
    `latest_seq`は今の合意の記録の順(確かめの印と照らす)。"""

    key: str
    requirement_id: str
    by_numbers: tuple[int, ...]
    base_text: str
    current_text: str | None
    latest_seq: int


def base_seq(records: Iterable[RecordFact], baseline_id: Any) -> int | None:
    """基準の束を合意にした記録の、最後の順(束の記録が無ければ None)。"""
    seqs = [r.seq for r in records if r.origin == "baseline" and r.baseline_id == baseline_id]
    return max(seqs) if seqs else None


def changed_after(
    records: Sequence[RecordFact], after_seq: int, *, exclude_number: int | None = None
) -> dict[str, list[RecordFact]]:
    """`after_seq`より後に、CR の反映が中身を書き換えた記録(要素のキー → 記録の順)。前の記録と
    中身が同じ行は入れない。`exclude_number`の CR の記録は入れない(自分の反映は競合でない)。"""
    changed: dict[str, list[RecordFact]] = {}
    for record in _changing(records):
        if (
            record.seq > after_seq
            and record.origin == "change_request"
            and record.change_request_number is not None
            and record.change_request_number != exclude_number
        ):
            changed.setdefault(record.key, []).append(record)
    return changed


def conflicts(
    requirement_ids: Sequence[str],
    records: Sequence[RecordFact],
    baseline_id: Any,
    own_number: int,
) -> list[Conflict]:
    """対象の要件(`requirement_ids`の順)のうち、基準の束の後にほかの CR が変えたもの。`records`は
    プロジェクトの合意の記録(順は問わない)。基準の束の記録が無ければ競合なし。"""
    ordered = sorted(records, key=lambda r: r.seq)
    start = base_seq(ordered, baseline_id)
    if start is None:
        return []
    changed = changed_after(ordered, start, exclude_number=own_number)
    found = []
    for req_id in requirement_ids:
        key = f"req:{req_id}"
        by_cr = changed.get(key)
        if not by_cr:
            continue
        keyed = [r for r in ordered if r.key == key]
        base = next((r for r in reversed(keyed) if r.seq <= start), None)
        # 印と照らすのは、中身が最後に変わった記録(同じ中身の行が足されても印は外れない)
        latest = _changing(keyed)[-1]
        found.append(
            Conflict(
                key=key,
                requirement_id=req_id,
                by_numbers=tuple(dict.fromkeys(r.change_request_number or 0 for r in by_cr)),
                base_text=_text(base.content) if base is not None else "",
                current_text=_text(latest.content) if latest.content is not None else None,
                latest_seq=latest.seq,
            )
        )
    return found


def is_checked(conflict: Conflict, checks: Collection[Mapping[str, Any]]) -> bool:
    """確かめの印が、今の合意の記録(`latest_seq`)に対して付いているか。"""
    return any(
        c.get("key") == conflict.key and c.get("seq") == conflict.latest_seq for c in checks
    )


def unchecked(
    found: Sequence[Conflict], checks: Collection[Mapping[str, Any]]
) -> list[Conflict]:
    """確かめの印の無い(または印の後にさらに変わった)競合。"""
    return [c for c in found if not is_checked(c, checks)]


def checks_for(found: Sequence[Conflict]) -> list[dict[str, Any]]:
    """今の競合をすべて確かめた、という印(CR の`conflict_checks`に保存する形)。"""
    return [{"key": c.key, "seq": c.latest_seq} for c in found]


def _changing(records: Iterable[RecordFact]) -> list[RecordFact]:
    """中身が同じキーの前の記録と違う記録(キーの最初の記録を含む。記録の順)。"""
    previous: dict[str, Mapping[str, Any] | None] = {}
    found = []
    for record in sorted(records, key=lambda r: r.seq):
        if record.key not in previous or previous[record.key] != record.content:
            found.append(record)
        previous[record.key] = record.content
    return found


def _text(content: Mapping[str, Any] | None) -> str:
    return str((content or {}).get("text", ""))
