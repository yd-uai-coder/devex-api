"""見直しの印と「確認済み」(純粋関数)。

docs/external_design.md 2.10節・docs/internal_design.md 3.3節「7. 変更影響分析・変更管理」。

段階を承認したとき(写しと今の model の差分)と、文書の表示中の版が切り替わったとき(前に表示して
いた版との差分)に、変更の影響を求め、届いた段階に印として記録する。写しは直前の1件だけなので、
差分はその時に取り、印は段階の行(`design_stages.impact`)に残す(上流を承認し直すと写しが置き換わり、
後から差分を取り直せないため)。

- 印の出どころ(`sources`)は`stage:<N>`(段階の承認)か`doc:<文書の種類>`(文書の版の切り替え)。
- 同じ要素に新しい変更が届いたら、未確認に戻す(確認したのは前の変更だけのため)。
- 段階の承認を止めるのは、他の段階・文書から届いた未確認の印だけ。自分の承認で付いた印(同じ段階の
  中の依存)は止めず、次の承認で置き換える。
- 段階を承認すると、その段階の印は消える(サービス層が`impact`を空にする)。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from app.detailed_design.impact.diff import ElementChange, to_trace_changes
from app.detailed_design.impact.elements import DOCUMENT_STAGE, Element, Ref
from app.detailed_design.impact.trace import impact_of


@dataclass(frozen=True)
class Mark:
    """見直しが要る要素1つの印。`path`は変わった要素からこの要素までの道(先頭が変わった要素)。"""

    key: str
    label: str
    stage: int
    path: tuple[str, ...]
    reason: str
    sources: tuple[str, ...]
    reviewed: bool = False


@dataclass(frozen=True)
class RecordedChange:
    """印のもとになった変更1つと、その出どころ。"""

    source: str
    change: ElementChange


@dataclass(frozen=True)
class StageImpact:
    """段階1つに記録した、変わったものと印(`design_stages.impact`の形)。"""

    changes: tuple[RecordedChange, ...] = ()
    marks: tuple[Mark, ...] = ()

    @property
    def open_count(self) -> int:
        return sum(1 for m in self.marks if not m.reviewed)

    @property
    def reviewed_count(self) -> int:
        return sum(1 for m in self.marks if m.reviewed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changes": [{"source": c.source, **asdict(c.change)} for c in self.changes],
            "marks": [asdict(m) for m in self.marks],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "StageImpact":
        """保存された形から作る。形の正しくない項目は捨てる(印の記録で段階を壊さないため)。"""
        if not value:
            return cls()
        changes: list[RecordedChange] = []
        for item in value.get("changes") or []:
            try:
                data = dict(item)
                source = str(data.pop("source"))
                changes.append(RecordedChange(source, ElementChange(**data)))
            except (TypeError, KeyError, ValueError):
                continue
        marks: list[Mark] = []
        for item in value.get("marks") or []:
            try:
                data = dict(item)
                data["path"] = tuple(data.get("path") or ())
                data["sources"] = tuple(data.get("sources") or ())
                marks.append(Mark(**data))
            except (TypeError, ValueError):
                continue
        return cls(tuple(changes), tuple(marks))


def stage_source(stage: int) -> str:
    return f"stage:{stage}"


def doc_source(doc_type: str) -> str:
    return f"doc:{doc_type}"


def merged_elements(before: Iterable[Element], after: Iterable[Element]) -> list[Element]:
    """前と後の要素を合わせたグラフ。同じキーは後の要素に、前の辺も足す(消えた要素・改名の前の
    キーを、まだ参照している要素から見つけるため)。前にだけ在る要素も残す。"""
    old = {e.key: e for e in before}
    merged: dict[str, Element] = {}
    for e in after:
        previous = old.get(e.key)
        refs = e.refs if previous is None else _union(e.refs, previous.refs)
        merged[e.key] = replace(e, refs=refs)
    for key, e in old.items():
        merged.setdefault(key, e)
    return list(merged.values())


def _union(first: Sequence[Ref], second: Sequence[Ref]) -> tuple[Ref, ...]:
    return tuple(dict.fromkeys([*first, *second]))


def marks_for(
    before: Sequence[Element],
    after: Sequence[Element],
    changes: Sequence[ElementChange],
    source: str,
) -> list[Mark]:
    """変わったものから、見直しが要る要素の印を求める(置き場の順、同じ置き場の中はキーの順)。
    文書の要素(置き場 0)と、変わった要素そのものには付けない(変えた人が見ているため)。"""
    graph = merged_elements(before, after)
    by_key = {e.key: e for e in graph}
    changed = {c.key for c in changes} | {c.new_key for c in changes if c.new_key}
    found: dict[str, Mark] = {}
    for change in to_trace_changes(changes):
        for item in impact_of(graph, change):
            element = by_key.get(item.key)
            if element is None or element.stage == DOCUMENT_STAGE or item.key in changed:
                continue
            previous = found.get(item.key)
            if previous is None or len(item.path) < len(previous.path):
                found[item.key] = Mark(
                    item.key, element.label, element.stage, item.path, item.reason, (source,)
                )
    return sorted(found.values(), key=lambda m: (m.stage, m.key))


def record(
    existing: StageImpact,
    source: str,
    changes: Sequence[ElementChange],
    marks: Sequence[Mark],
) -> StageImpact:
    """段階1つの記録に、出どころ`source`の新しい変更と印を足す。同じ要素の印は、新しい道・理由にして
    出どころを足し、未確認に戻す。変更は、この段階に印を付けたもの(印の道の先頭)だけを残す。"""
    by_key = {m.key: m for m in existing.marks}
    for mark in marks:
        previous = by_key.get(mark.key)
        sources = (source,) if previous is None else _merge(previous.sources, (source,))
        by_key[mark.key] = replace(mark, sources=sources, reviewed=False)
    heads = {m.path[0] for m in marks if m.path}
    added = [RecordedChange(source, c) for c in changes if c.key in heads]
    replaced = {(c.source, c.change.key) for c in added}
    kept = [c for c in existing.changes if (c.source, c.change.key) not in replaced]
    return StageImpact(
        tuple(kept + added), tuple(sorted(by_key.values(), key=lambda m: (m.stage, m.key)))
    )


def _merge(first: Sequence[str], second: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys([*first, *second]))


def set_reviewed(impact: StageImpact, keys: Iterable[str], reviewed: bool) -> StageImpact:
    """印を確認済み(または未確認)にする。知らないキーは無視する。"""
    wanted = set(keys)
    marks = tuple(replace(m, reviewed=reviewed) if m.key in wanted else m for m in impact.marks)
    return replace(impact, marks=marks)


def blocking_marks(impact: StageImpact, stage: int) -> list[Mark]:
    """段階の承認を止める印: 他の段階・文書から届いた、未確認の印。"""
    own = stage_source(stage)
    return [m for m in impact.marks if not m.reviewed and any(s != own for s in m.sources)]


def group_by_stage(marks: Iterable[Mark]) -> dict[int, list[Mark]]:
    grouped: dict[int, list[Mark]] = {}
    for mark in marks:
        grouped.setdefault(mark.stage, []).append(mark)
    return grouped

