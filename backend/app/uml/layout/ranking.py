"""レーン/行の自動割り当て(devex独自の新規アルゴリズム)。

移植元の図生成エンジンはlane/rowを人手で指定する前提で、自動割り当て機構を持たない。
devexの図はAI/ユーザー生成の意味モデルから組み立てるため、
lane(レーン)・row(行)をこちらで算出する必要がある
(appendix/stage3-requirements-organization.md 診断3)。

- lane: 要素の`layer`属性(component/dfdの一部要素が持つ)。未設定の要素は共有の
  フォールバックレーンにまとめる。ER図は`layer`を持たないため常に単一レーン。
- row: relationsから構築した有向グラフの最長経路法によるレイヤリング。ただしレーンごとに
  1つの行には1ノードしか置かない(`geometry.place_rows`はノードをレーンの中央・行の縦位置に
  置くため、同じ`(lane, row)`のノードは完全に重なる)。同じレーンで行が衝突したら、
  そのレーンの次の空き行へ下げる(循環はDFSで検出したback edgeを除外して計算。
  トポロジカル順の層分けは標準ライブラリ`graphlib`に任せる)。ER図は多重度を表すだけで時系列を持たないため、
  このアルゴリズムを使わず意味モデル内の定義順indexをそのまま使う
  (このセッションでの決定。textbook/decision-digest.md参照)。
"""

from collections.abc import Sequence
from graphlib import TopologicalSorter

from app.uml.domain.base import NotationType, UmlElement, UmlRelation

# lane未設定の要素をまとめるフォールバックレーンのラベル(Noneはレーン見出し無し扱い)
_FALLBACK_LANE_LABEL: str | None = None


def _find_back_edges(
    node_ids: Sequence[str], edges: Sequence[tuple[str, str]]
) -> set[tuple[str, str]]:
    """DFSで木の祖先へ戻る辺(back edge)を検出する。ランク計算からこれらを除外することで、
    循環を含むグラフでも有向非巡回グラフとして層分けできるようにする(GraphViz/dotのrank
    計算等で使われる一般的な近似手法。最小feedback arc setの厳密解ではない)。

    networkxは使わない: `dfs_labeled_edges`はback edgeを交差辺・前進辺と区別せず`nontree`に
    まとめるため結局この判定を自前で書く必要があり、import だけで十数MBを消費する割に得がない。
    """
    adjacency: dict[str, list[str]] = {n: [] for n in node_ids}
    for a, b in edges:
        adjacency.setdefault(a, []).append(b)

    state: dict[str, int] = {}  # 0=未訪問(省略) / 1=訪問中 / 2=完了
    back_edges: set[tuple[str, str]] = set()

    def visit(u: str) -> None:
        state[u] = 1
        for v in adjacency.get(u, []):
            if state.get(v, 0) == 0:
                visit(v)
            elif state.get(v) == 1:
                back_edges.add((u, v))
        state[u] = 2

    for n in node_ids:
        if state.get(n, 0) == 0:
            visit(n)
    return back_edges


def _assign_rows(
    node_ids: Sequence[str], edges: Sequence[tuple[str, str]], lane_of: dict[str, int]
) -> dict[str, int]:
    """各ノードのrow(行)を、レーンごとに1行1ノードの最長経路法で算出する。

    トポロジカル順(同じ層の中は`node_ids`の定義順)に1つずつ置き、行は
    「全ての前提ノードの行+1」以上で、そのノードのレーンでまだ使われていない最小の行にする。
    衝突が無ければ最長経路法の層番号と一致し、衝突したときだけ下へずれる。前提ノードより
    必ず下に置くので、辺の向き(上から下)は保たれる。循環はback edgeとして除外する。
    """
    back_edges = _find_back_edges(node_ids, edges)
    order = {n: i for i, n in enumerate(node_ids)}
    predecessors: dict[str, list[str]] = {n: [] for n in node_ids}
    sorter: TopologicalSorter[str] = TopologicalSorter(dict.fromkeys(node_ids, ()))
    for a, b in edges:
        if (a, b) not in back_edges:
            sorter.add(b, a)  # bはaの後(aが前提)
            predecessors.setdefault(b, []).append(a)
    sorter.prepare()

    row: dict[str, int] = {}
    taken: set[tuple[int, int]] = set()  # 使用済みの(lane, row)
    while sorter.is_active():
        # get_ready()の返す順は文書化されていないため、定義順に並べて決定的にする
        ready = sorted(sorter.get_ready(), key=lambda n: order.get(n, len(order)))
        for n in ready:
            r = max((row[p] + 1 for p in predecessors.get(n, [])), default=0)
            while (lane_of[n], r) in taken:
                r += 1
            row[n] = r
            taken.add((lane_of[n], r))
        sorter.done(*ready)
    return row


def assign_lanes_and_rows(
    notation: NotationType,
    elements: Sequence[UmlElement],
    relations: Sequence[UmlRelation],
) -> tuple[list[str | None], dict[str, int], dict[str, int]]:
    """要素ごとのlane index・row indexを算出する。

    戻り値: (lane_labels, lane_of, row_of)。`lane_of`/`row_of`は要素idをキーにした辞書。
    """
    element_ids = [el.id for el in elements]

    if notation == "er":
        # ER図の関係(多重度)は時系列を持たないため、最長経路法は使わず
        # 単一レーン+定義順indexという機械的なフォールバックにする(このセッションでの決定)。
        lane_of = dict.fromkeys(element_ids, 0)
        row_of = {el_id: i for i, el_id in enumerate(element_ids)}
        return [_FALLBACK_LANE_LABEL], lane_of, row_of

    # レーン: layer属性の初出順を並び順にする。layer未設定の要素は共有のフォールバックレーン
    # (末尾に追加)にまとめる。
    lane_labels: list[str | None] = []
    for el in elements:
        layer = getattr(el, "layer", None)
        if layer is not None and layer not in lane_labels:
            lane_labels.append(layer)
    has_unassigned = any(getattr(el, "layer", None) is None for el in elements)
    if has_unassigned:
        lane_labels.append(_FALLBACK_LANE_LABEL)

    lane_index = {label: i for i, label in enumerate(lane_labels)}
    lane_of = {}
    for el in elements:
        layer = getattr(el, "layer", None)
        key = layer if layer is not None else _FALLBACK_LANE_LABEL
        lane_of[el.id] = lane_index[key]

    # 行: relationsから有向グラフを組み立て、最長経路法でレイヤリングする。
    edges = [(rel.source_id, rel.target_id) for rel in relations]
    row_of = _assign_rows(element_ids, edges, lane_of)

    return lane_labels, lane_of, row_of
