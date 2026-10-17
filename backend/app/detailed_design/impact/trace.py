"""変更の影響のたどり方と理由(純粋関数)。

docs/external_design.md 2.10節・docs/internal_design.md 3.3節「7. 変更影響分析・変更管理」。

ある要素を変えたとき、見直しが要る要素を「処理を軸」の規則で求める(Phase 41 のベースラインで、
辺を全部たどると広すぎ、直接だけでは狭すぎたため)。

1. 変わった要素の直接の参照元(1段)は、種類によらず示す。
2. 改名(参照の文字列だけが変わる変更)は、1 だけで止める。
3. テーブルの変更は処理まで上らない。テーブルを読み書きする要素(1)と、その手順を実装する処理
   ロジック・手順書までを示す(処理のすべての要素を示すと、テーブルを使う処理の数だけ広がるため)。
4. 変更が届く処理(段階1)を決める。変わった要素が処理ならそれ自身、文書の要素(要件・画面・API)
   なら、文書の要素だけを通って届く処理。ただし画面は、変わった要素そのもののときだけ通る(要件が
   共有の画面を通って、別の処理へ広がらないため)。モジュールなら、それを使う CRUD・手順の処理。
5. 届いた処理に属する要素(要約・CRUD・手順・作業単位)と、その先の処理ロジック・手順書を示す。
   モジュールは処理に属するが、共有されるので示さず、その先へも広げない。文書の要素の変更では、
   CRUD と作業単位を示さない(テーブルの使い方と作業の分け方は文書の変更で変わらないため)。
   要件・画面の変更では要約も示さない(要約は入出力の表で、直接変えるのは API の変更だけのため)。
6. 横断事項は処理を持たないので、横断事項の参照元(対象のモジュールを含む手順書)まで示す。

簡易ドキュメントモード(simple.py の要素)では、処理別データフロー(DF)を処理の代わりの軸にする。
実装計画書のタスク(置き場0)も、要件を直接持つ(詳細設計モードで処理が要件を持つのに当たる)ので
軸に含める。軸に届けば、そのタスク・単位の手順書を示す。WBS の依存とモジュールの共有からは広げない。

理由は、たどった最後の辺の種類からコードが文にする(LLM は使わない)。同じ要素に複数の道があれば、
短い道を残す。
"""

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.detailed_design.impact.elements import DOCUMENT_STAGE, EdgeKind, Element, ElementKind

# 変更の種類。content = 内容の変更、rename = 改名(参照の文字列だけが変わる)
ChangeKind = Literal["content", "rename"]

EDGE_REASON: dict[EdgeKind, str] = {
    "requirement": "この要件を持つ",
    "screen": "この画面に関わる",
    "trigger": "この API で起動する",
    "function": "この処理に属する",
    "table": "このテーブルを読み書きする",
    "depends": "このモジュールに依存する",
    "calls": "このモジュールを呼ぶ",
    "placed": "このモジュールに置く",
    "called_by": "この手順から呼ばれる",
    "implements": "この設計を手順書で実装する",
    "unit": "この作業単位の手順書",
    "covers": "この横断事項の対象のモジュールを含む",
    "receives": "この API を受ける",
}

# テーブル・モジュールの変更が、それを使う処理に届くときの理由
USER_REASON = "変わった要素を使う処理"

# 処理への道をたどるときに通ってよい要素(文書の要素)
_TOWARD_FUNCTION: frozenset[ElementKind] = frozenset({"requirement", "screen", "api"})
# 処理に属する要素の、さらに先で示す要素(手順を呼ぶ処理ロジック・作業単位の手順書)
_FOLLOW_OWN: frozenset[ElementKind] = frozenset({"logic", "unit"})
# テーブルの変更で、読み書きする要素の先にたどる辺(手順 → 処理ロジック・手順書)
_IMPLEMENTED_BY: frozenset[EdgeKind] = frozenset({"called_by", "implements"})
# 文書の要素の変更で、届いた処理に属していても示さない要素(作業単位は軸でないものだけ)
_HIDDEN_OWN: dict[ElementKind, frozenset[ElementKind]] = {
    "requirement": frozenset({"crud", "task", "summary"}),
    "screen": frozenset({"crud", "task", "summary"}),
    "api": frozenset({"crud", "task"}),
}
# 部分再生成できる段階(5 手順・6 処理ロジック・8 単位の手順書)
REGENERATION_STAGES: frozenset[int] = frozenset({5, 6, 8})


@dataclass(frozen=True)
class Change:
    """変わった要素1つ(要素のキーと変更の種類)。"""

    key: str
    kind: ChangeKind = "content"


@dataclass(frozen=True)
class ImpactItem:
    """見直しが要る要素1つ。`path`は変わった要素からこの要素までの道(先頭が変わった要素)。"""

    key: str
    path: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class _Back:
    src: str
    edge: EdgeKind


def _back_index(elements: Iterable[Element]) -> dict[str, list[_Back]]:
    """参照先 → 参照元(辺の逆向き)。"""
    index: dict[str, list[_Back]] = {}
    for element in elements:
        for ref in element.refs:
            index.setdefault(ref.to, []).append(_Back(element.key, ref.edge))
    return index


def impact_of(elements: Sequence[Element], change: Change) -> list[ImpactItem]:
    """変更から、見直しが要る要素を求める(置き場の順、同じ置き場の中はキーの順)。"""
    by_key = {e.key: e for e in elements}
    back = _back_index(elements)
    found: dict[str, ImpactItem] = {}

    def kind_of(key: str) -> ElementKind | None:
        return _kind(by_key, key)

    def add(path: tuple[str, ...], reason: str) -> None:
        key = path[-1]
        if key == change.key:
            return
        previous = found.get(key)
        if previous is None or len(path) < len(previous.path):
            found[key] = ImpactItem(key, path, reason)

    direct = back.get(change.key, [])
    for b in direct:
        add((change.key, b.src), EDGE_REASON[b.edge])
    if change.kind == "rename":
        return _sorted(found.values(), by_key)
    changed_kind = kind_of(change.key)
    if changed_kind == "table":
        _add_implementers(change.key, direct, back, add)
        return _sorted(found.values(), by_key)

    for b in direct:
        if kind_of(b.src) == "crosscutting":
            for u in back.get(b.src, []):
                add((change.key, b.src, u.src), EDGE_REASON[u.edge])

    hidden = _HIDDEN_OWN.get(changed_kind, frozenset()) if changed_kind else frozenset()
    functions = _reached_functions(change, direct, back, by_key)
    for fid, (path, reason) in functions.items():
        if fid != change.key:
            add(path, reason)
        for own in back.get(fid, []):
            element = by_key.get(own.src)
            if element is None or element.kind == "module":
                continue
            if element.kind not in hidden or _is_axis(element):
                add((*path, own.src), EDGE_REASON[own.edge])
            for nxt in back.get(own.src, []):
                if kind_of(nxt.src) in _FOLLOW_OWN:
                    add((*path, own.src, nxt.src), EDGE_REASON[nxt.edge])
    return _sorted(found.values(), by_key)


def _add_implementers(
    table: str,
    direct: list[_Back],
    back: Mapping[str, list[_Back]],
    add: Callable[[tuple[str, ...], str], None],
) -> None:
    """テーブルを読み書きする要素から、それを実装する処理ロジック・手順書へ(処理には上らない)。"""
    queue: deque[tuple[str, ...]] = deque((table, b.src) for b in direct)
    seen = {table, *(b.src for b in direct)}
    while queue:
        path = queue.popleft()
        for b in back.get(path[-1], []):
            if b.edge in _IMPLEMENTED_BY and b.src not in seen:
                seen.add(b.src)
                add((*path, b.src), EDGE_REASON[b.edge])
                queue.append((*path, b.src))


def _reached_functions(
    change: Change,
    direct: list[_Back],
    back: Mapping[str, list[_Back]],
    by_key: Mapping[str, Element],
) -> dict[str, tuple[tuple[str, ...], str]]:
    """変更が届く処理(処理のキー → 道と理由)。文書の要素だけを通る幅優先で、短い道から見つける。"""
    functions: dict[str, tuple[tuple[str, ...], str]] = {}
    if _is_axis(by_key.get(change.key)):
        functions[change.key] = ((change.key,), "")
    queue: deque[tuple[str, ...]] = deque([(change.key,)])
    while queue:
        path = queue.popleft()
        for b in back.get(path[-1], []):
            if b.src in path:
                continue
            kind = _kind(by_key, b.src)
            if _is_axis(by_key.get(b.src)):
                if b.src not in functions:
                    functions[b.src] = ((*path, b.src), EDGE_REASON[b.edge])
            elif kind in _TOWARD_FUNCTION and kind != "screen":
                queue.append((*path, b.src))
    for b in direct:
        user = by_key.get(b.src)
        if user is None or user.kind not in ("crud", "procedure"):
            continue
        for ref in user.refs:
            if ref.edge == "function" and ref.to not in functions:
                functions[ref.to] = ((change.key, b.src, ref.to), USER_REASON)
    return functions


def _is_axis(element: Element | None) -> bool:
    """たどり方の軸か。処理(段階1)・簡易モードの処理別データフローと、文書のタスク(簡易モード)。"""
    if element is None:
        return False
    if element.kind in ("function", "dataflow"):
        return True
    return element.kind == "task" and element.stage == DOCUMENT_STAGE


def path_axis(path: Sequence[str], by_key: Mapping[str, Element]) -> str | None:
    """道の上にある最初の軸(処理・処理別データフロー・文書のタスク)。無ければ None。"""
    return next((key for key in path if _is_axis(by_key.get(key))), None)


def _kind(by_key: Mapping[str, Element], key: str) -> ElementKind | None:
    element = by_key.get(key)
    return element.kind if element is not None else None


def _sorted(items: Iterable[ImpactItem], by_key: Mapping[str, Element]) -> list[ImpactItem]:
    def stage(item: ImpactItem) -> int:
        element = by_key.get(item.key)
        return element.stage if element is not None else 0

    return sorted(items, key=lambda item: (stage(item), item.key))


def regeneration_targets(
    elements: Sequence[Element], items: Iterable[ImpactItem]
) -> list[ImpactItem]:
    """再生成の候補: 部分再生成できる段階(5・6・8)の要素。自動では再生成しない。"""
    stages = {e.key: e.stage for e in elements}
    return [item for item in items if stages.get(item.key) in REGENERATION_STAGES]
