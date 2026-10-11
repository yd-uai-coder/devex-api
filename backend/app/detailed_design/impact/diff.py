"""変わったものの求め方(承認時の写しと今の model の差分。純粋関数)。

docs/external_design.md 2.10節・docs/internal_design.md 3.3節「7. 変更影響分析・変更管理」。

前と後の要素(`build_elements`)を、キーで突き合わせる。比べるのは要素の内容(`Element.body`)だけで、
辺は比べない(手順の行から導くモジュール → テーブルの辺のように、別の段階の変更でも辺は変わるため)。

- 同じキーで内容が違う → 内容の変更(content)
- 消えたキーと増えたキーが、同じ種類・同じ内容で1対1に対応する → 改名(rename)。ID が名前そのもの
  である種類(`_RENAMEABLE`)だけを見る。内容の薄い種類(CRUD・要約など)は、別の行と偶然そろうため
  改名とみなさない
- 残り → 消えた(removed)・増えた(added)
- 改名に合わせて参照の文字列を書き換えただけの内容の変更(依存先のパスを新しい名前にした、など)は、
  変わったものに入れない(改名の直接の参照元として印が付く。内容の変更として処理へ広げないため)

`to_trace_changes`は、たどり方(`impact_of`)に渡す変更にする。増えた要素は、まだ誰からも参照されない
ので影響を求めない。消えた要素は、前のキーで内容の変更として求める(参照元が見直しの対象)。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.detailed_design.impact.elements import Element, ElementKind
from app.detailed_design.impact.trace import Change

# 差分の種類
DiffKind = Literal["added", "removed", "content", "rename"]

# 改名とみなしてよい種類(ID が名前そのもので、内容が十分に厚い)
_RENAMEABLE: frozenset[ElementKind] = frozenset(
    {"requirement", "screen", "api", "function", "table", "module", "logic", "crosscutting"}
)


@dataclass(frozen=True)
class ElementChange:
    """変わった要素1つ。`key`は前のキー(増えたものは後のキー)、改名の後のキーは`new_key`。
    `before`・`after`は要素の内容(`Element.body`。無い側は None)。"""

    key: str
    change: DiffKind
    stage: int
    label: str
    before: str | None = None
    after: str | None = None
    new_key: str | None = None


def diff_elements(
    before: Iterable[Element], after: Iterable[Element], *, stages: Iterable[int] | None = None
) -> list[ElementChange]:
    """前と後の要素の差分(置き場の順、同じ置き場の中はキーの順)。`stages`を渡すと、その置き場の
    要素だけを比べる(段階の承認では、その段階の要素だけが変わったものになるため)。"""
    wanted = set(stages) if stages is not None else None
    old = {e.key: e for e in before if wanted is None or e.stage in wanted}
    new = {e.key: e for e in after if wanted is None or e.stage in wanted}
    removed = [old[k] for k in old.keys() - new.keys()]
    added = [new[k] for k in new.keys() - old.keys()]
    renamed = _renames(removed, added)
    names = {_name(k): _name(e.key) for k, e in renamed.items()}
    changes: list[ElementChange] = []
    for key in old.keys() & new.keys():
        a, b = old[key], new[key]
        if a.body != b.body and _renamed_body(a.body, names) != b.body:
            changes.append(ElementChange(key, "content", b.stage, b.label, a.body, b.body))
    for a in removed:
        b = renamed.get(a.key)
        if b is not None:
            changes.append(
                ElementChange(a.key, "rename", b.stage, b.label, a.label, b.label, new_key=b.key)
            )
        else:
            changes.append(ElementChange(a.key, "removed", a.stage, a.label, a.body, None))
    paired = {b.key for b in renamed.values()}
    for b in added:
        if b.key not in paired:
            changes.append(ElementChange(b.key, "added", b.stage, b.label, None, b.body))
    return sorted(changes, key=lambda c: (c.stage, c.key))


def _name(key: str) -> str:
    """キーから種類の接頭辞を除いた名前(内容の中で参照に使う文字列)。"""
    return key.split(":", 1)[1]


def _renamed_body(body: str, names: dict[str, str]) -> str:
    """内容の中の参照(JSON の文字列の値)を、改名の後の名前に書き換える。"""
    for old, new in names.items():
        body = body.replace(f'"{old}"', f'"{new}"')
    return body


def _renames(removed: Sequence[Element], added: Sequence[Element]) -> dict[str, Element]:
    """消えた要素 → 改名の後の要素。同じ種類・同じ内容の組が1対1のときだけ。"""
    def signature(e: Element) -> tuple[str, str]:
        return (e.kind, e.body)

    olds: dict[tuple[str, str], list[Element]] = {}
    news: dict[tuple[str, str], list[Element]] = {}
    for e in removed:
        if e.kind in _RENAMEABLE:
            olds.setdefault(signature(e), []).append(e)
    for e in added:
        if e.kind in _RENAMEABLE:
            news.setdefault(signature(e), []).append(e)
    return {
        group[0].key: news[sig][0]
        for sig, group in olds.items()
        if len(group) == 1 and len(news.get(sig, [])) == 1
    }


def to_trace_changes(changes: Iterable[ElementChange]) -> list[Change]:
    """たどり方に渡す変更(増えた要素は除く。消えた要素は前のキーの内容の変更)。"""
    return [
        Change(c.key, "rename" if c.change == "rename" else "content")
        for c in changes
        if c.change != "added"
    ]
