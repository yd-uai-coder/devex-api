"""行の入れ替えによる交差削減。出自: 別プロジェクトの自作図生成エンジンから移植。

行を人が手で配置する図なら、その意図を守るために「入れ替えてよいノード」を明示的に
宣言させる必要がある。devexでは行(row)自体が`ranking.py`によって全て自動算出されるため、
保護すべき「人の意図」が無い。よって**全要素を交差削減の対象にする**(このセッションでの決定)。
"""

import itertools

from app.uml.layout.model import LayoutState
from app.uml.layout.pipeline import route

# 重み付け(交差の削減を最優先し、重なり・ラベル配置の
# フォールバックは副次的な指標として扱う)。
_CROSSING_WEIGHT = 1000
_OVERLAP_WEIGHT = 300
_LABEL_FALLBACK_WEIGHT = 50
_SCORE_THRESHOLD = 1000  # スコアがこれ未満まで下がったら十分とみなし打ち切る


def _score(state: LayoutState) -> float:
    route(state)
    r = state.report
    return (
        r["crossings"] * _CROSSING_WEIGHT
        + r["overlaps"] * _OVERLAP_WEIGHT
        + len(r.get("label_fallback", [])) * _LABEL_FALLBACK_WEIGHT
    )


def optimize(state: LayoutState) -> None:
    """同じレーンの要素同士で行を入れ替える山登りにより、交差数を減らす。
    (1) ペアの行を交換して改善するか試す、(2) 各要素を同じレーン内の空いている行へ
    移動して改善するか試す、を交差が十分減るか改善が止まるまで繰り返す。
    """
    free = list(state.nodes.keys())
    best = _score(state)
    improved = True
    while improved and best >= _SCORE_THRESHOLD:
        improved = False
        for a, b in itertools.combinations(sorted(free), 2):
            na, nb = state.nodes[a], state.nodes[b]
            if na.lane != nb.lane:
                continue
            na.row, nb.row = nb.row, na.row
            sc = _score(state)
            if sc < best:
                best, improved = sc, True
            else:
                na.row, nb.row = nb.row, na.row
        # 同じレーンの空いている行への移動も試す
        maxrow = max(n.row for n in state.nodes.values()) + 1
        for a in sorted(free):
            na = state.nodes[a]
            taken = {n.row for n in state.nodes.values() if n.lane == na.lane and n is not na}
            old = na.row
            for r in range(maxrow + 1):
                if r == old or r in taken:
                    continue
                na.row = r
                sc = _score(state)
                if sc < best:
                    best, improved, old = sc, True, r
                else:
                    na.row = old
    _score(state)  # 最良の配置で再ルーティング
