"""変更要求(CR)に結ぶ影響の予測(純粋関数。docs/external_design.md 2.14節)。

合意の前に、基準の合意済みの版(束)の写しから設計の要素を組み、CR の対象の要件ごとに今の変更影響
分析のたどり方(`impact_of`。処理を軸)で見直しが要る要素を求めて、1つの一覧にまとめる。LLM は
使わず、束は凍結されているので、同じ CR からはいつも同じ予測になる。

- 変更の種類(内容の変更・削除)によらず、内容の変更としてたどる(反映の後の差分でも、消えた要素は
  内容の変更としてたどるため。予測と実際の印を同じ規則にそろえる)。
- 画面・API(文書の側の要素)も入る(再生成の印は文書の側に付かないため、予測で示す)。
- 新しい要件を足す分は起点の要素が無いので予測に入らない(`unpredicted_new_requirement`で示す)。
- 保存の形(`to_dict`・`from_dict`)は、判断(合意・却下)の時点で CR に残すときに使う。
- CR の反映で書き換えてよい要素(`rewritable_keys`)は、対象の要件と、保存した予測の文書の側の
  要素(画面・API)。合意済みの要素の保護から外し、反映で合意を更新する(docs/external_design.md
  2.15節)。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.change_requests.targets import TARGET_KINDS, Target, TargetKind
from app.detailed_design import DATA_MODEL_STAGE, ER_SNAPSHOT_KEY
from app.detailed_design.impact import (
    DOCUMENT_STAGE,
    Change,
    DesignModels,
    Element,
    ElementKind,
    build_elements,
    impact_of,
)

# 束の写しの key(文書)と、要素を組む文書の種類
DOCUMENT_KEYS: tuple[str, ...] = ("requirements", "external_design")
# 束の写しの段階の key の接頭辞(`stage:3`)
STAGE_PREFIX = "stage:"


@dataclass(frozen=True)
class PredictedTarget:
    """予測の起点の要件1つ(ID・変更の種類・束の要件定義書の文。束に無ければ空)。"""

    requirement_id: str
    kind: TargetKind
    text: str


@dataclass(frozen=True)
class PredictedItem:
    """見直しが要る要素1つ。`path`は起点の要件からこの要素までの道(先頭が要件)、
    `requirement_ids`はこの要素に届いた対象の要件(対象の順)。"""

    key: str
    kind: ElementKind
    stage: int
    label: str
    path: tuple[str, ...]
    reason: str
    requirement_ids: tuple[str, ...]


@dataclass(frozen=True)
class Prediction:
    """CR 1つの予測。`items`は置き場の順・同じ置き場の中はキーの順。"""

    targets: tuple[PredictedTarget, ...]
    items: tuple[PredictedItem, ...]
    unpredicted_new_requirement: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": [
                {"requirement_id": t.requirement_id, "kind": t.kind, "text": t.text}
                for t in self.targets
            ],
            "items": [
                {
                    "key": i.key,
                    "kind": i.kind,
                    "stage": i.stage,
                    "label": i.label,
                    "path": list(i.path),
                    "reason": i.reason,
                    "requirement_ids": list(i.requirement_ids),
                }
                for i in self.items
            ],
            "unpredicted_new_requirement": self.unpredicted_new_requirement,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Prediction":
        return cls(
            targets=tuple(
                PredictedTarget(
                    requirement_id=str(t["requirement_id"]),
                    kind=t["kind"] if t.get("kind") in TARGET_KINDS else "content",
                    text=str(t.get("text", "")),
                )
                for t in value.get("targets", [])
            ),
            items=tuple(
                PredictedItem(
                    key=str(i["key"]),
                    kind=i["kind"],
                    stage=int(i["stage"]),
                    label=str(i.get("label", "")),
                    path=tuple(i.get("path", [])),
                    reason=str(i.get("reason", "")),
                    requirement_ids=tuple(i.get("requirement_ids", [])),
                )
                for i in value.get("items", [])
            ),
            unpredicted_new_requirement=bool(value.get("unpredicted_new_requirement", False)),
        )


def baseline_models(snapshots: Mapping[str, Mapping[str, Any] | None]) -> DesignModels:
    """束の写し(key → model。入っているものだけを渡す)から、要素を組む入力を作る。文書は
    要件定義書・外部設計書、段階は`stage:N`。ER は段階3の写しが持つ承認したときの定義を使う
    (無ければ ER のテーブルは要素にならない)。"""
    documents = {key: snapshots.get(key) for key in DOCUMENT_KEYS}
    stages: dict[int, Mapping[str, Any] | None] = {}
    for key, model in snapshots.items():
        if key.startswith(STAGE_PREFIX) and key[len(STAGE_PREFIX) :].isdigit():
            stages[int(key[len(STAGE_PREFIX) :])] = model
    er = (stages.get(DATA_MODEL_STAGE) or {}).get(ER_SNAPSHOT_KEY)
    return DesignModels.from_dicts(stages, documents, er if isinstance(er, Mapping) else ())


def requirement_texts(requirements: Mapping[str, Any] | None) -> dict[str, str]:
    """要件定義書の model の要件 ID → 文。"""
    rows = (requirements or {}).get("rows", [])
    return {str(row["id"]): str(row.get("text", "")) for row in rows if row.get("id")}


def predict(
    elements: Sequence[Element],
    targets: Sequence[Target],
    *,
    adds_requirement: bool,
    texts: Mapping[str, str] | None = None,
) -> Prediction:
    """対象の要件ごとに影響をたどり、1つの一覧にまとめる。同じ要素に複数の対象から届いたら、
    短い道(同じ長さなら先の対象)を残し、届いた対象をすべて記録する。対象の要件そのものは
    一覧に入れない。"""
    by_key = {e.key: e for e in elements}
    target_keys = {_requirement_key(t.requirement_id) for t in targets}
    best: dict[str, tuple[tuple[str, ...], str]] = {}
    reached: dict[str, list[str]] = {}
    for target in targets:
        for item in impact_of(elements, Change(_requirement_key(target.requirement_id))):
            if item.key in target_keys:
                continue
            previous = best.get(item.key)
            if previous is None or len(item.path) < len(previous[0]):
                best[item.key] = (item.path, item.reason)
            ids = reached.setdefault(item.key, [])
            if target.requirement_id not in ids:
                ids.append(target.requirement_id)
    items = []
    for key, (path, reason) in best.items():
        element = by_key[key]
        items.append(
            PredictedItem(
                key=key,
                kind=element.kind,
                stage=element.stage,
                label=element.label,
                path=path,
                reason=reason,
                requirement_ids=tuple(reached[key]),
            )
        )
    items.sort(key=lambda i: (i.stage, i.key))
    texts = texts or {}
    return Prediction(
        targets=tuple(
            PredictedTarget(t.requirement_id, t.kind, texts.get(t.requirement_id, ""))
            for t in targets
        ),
        items=tuple(items),
        unpredicted_new_requirement=adds_requirement,
    )


def predict_from_snapshots(
    snapshots: Mapping[str, Mapping[str, Any] | None],
    targets: Sequence[Target],
    *,
    adds_requirement: bool,
) -> Prediction:
    """束の写し(key → model。入っているものだけ)から要素を組んで予測する。対象の文は束の
    要件定義書から引く。"""
    elements = build_elements(baseline_models(snapshots))
    return predict(
        elements,
        targets,
        adds_requirement=adds_requirement,
        texts=requirement_texts(snapshots.get("requirements")),
    )


def _requirement_key(requirement_id: str) -> str:
    return f"req:{requirement_id}"


def rewritable_keys(
    targets: Sequence[Target], predicted_impact: Mapping[str, Any] | None
) -> frozenset[str]:
    """CR を反映する再生成で書き換えてよい要素のキー。対象の要件(内容の変更・削除)と、保存した
    予測(`predicted_impact`。無ければ対象の要件だけ)の文書の側の要素(画面・API)。"""
    keys = {_requirement_key(target.requirement_id) for target in targets}
    if predicted_impact:
        prediction = Prediction.from_dict(predicted_impact)
        keys |= {item.key for item in prediction.items if item.stage == DOCUMENT_STAGE}
    return frozenset(keys)
