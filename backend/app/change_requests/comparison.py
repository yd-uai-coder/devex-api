"""変更要求(CR)の影響の予測と、反映で実際に付いた印の比較(純粋関数)。

docs/external_design.md 2.14節「反映」。判断の時点で保存した予測(prediction.py)と、CR を渡した
文書の再生成で付いた印の写し(`change_requests.applied_impact`)を、要素のキーで突き合わせる。

- 一致(`matched`): 予測にも実際の印にもある。
- 予測だけ(`predicted_only`): 予測にあるが印が付いていない。理由を分ける。
  - `document_side`: 文書の側の要素(画面・API。置き場0)。今の印は文書の側に付かないため、
    印の有無では確かめられない(人が見る)。
  - `unmarked_stage`: 一度も承認していない段階の要素。印は承認した段階にだけ付くため。
  - `not_changed`: 印を付けられる段階なのに、反映の差分が届かなかった(予測の外れ、または
    書き直しが要らなかった)。
- 実際だけ(`actual_only`): 予測に無いのに印が付いた(見落とし、または CR の範囲の外の書き換え)。

確認の状態(`reviewed`)は、呼び出し元が渡した印の値(今の確認の状態。verification.py)と、文書の側の
確認済みのキー(`document_checks`)から出す。
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

from app.change_requests.prediction import Prediction
from app.detailed_design.impact import DOCUMENT_STAGE

ComparisonStatus = Literal["matched", "predicted_only", "actual_only"]
PredictedOnlyReason = Literal["document_side", "unmarked_stage", "not_changed"]

COMPARISON_STATUSES: tuple[ComparisonStatus, ...] = get_args(ComparisonStatus)
COMPARISON_STATUS_LABEL: dict[ComparisonStatus, str] = {
    "matched": "一致",
    "predicted_only": "予測だけ",
    "actual_only": "実際だけ(予測の外)",
}
PREDICTED_ONLY_REASON_LABEL: dict[PredictedOnlyReason, str] = {
    "document_side": "文書の側(印が付かない)",
    "unmarked_stage": "承認していない段階(印が付かない)",
    "not_changed": "反映の差分が届かなかった",
}


@dataclass(frozen=True)
class AppliedMark:
    """反映で付いた印1つ(保存の形から読んだもの)。"""

    key: str
    label: str
    stage: int
    reason: str
    reviewed: bool


@dataclass(frozen=True)
class ComparedItem:
    """要素1つの比較。`predicted_reason`・`actual_reason`はそれぞれの道の理由(無い側は空)。
    `requirement_ids`は予測で届いた対象の要件(実際だけの要素は空)。`reviewed`は印の確認の状態
    (文書の側は確認の一覧の状態。印が無く、文書の側でもなければ None)。"""

    key: str
    label: str
    stage: int
    status: ComparisonStatus
    predicted_only_reason: PredictedOnlyReason | None
    predicted_reason: str
    actual_reason: str
    requirement_ids: tuple[str, ...]
    reviewed: bool | None


@dataclass(frozen=True)
class Comparison:
    """CR 1つの比較。`items`は置き場の順・同じ置き場の中はキーの順。"""

    items: tuple[ComparedItem, ...]

    def count(self, status: ComparisonStatus) -> int:
        return sum(1 for item in self.items if item.status == status)


def applied_marks_from_json(value: Sequence[Mapping[str, Any]] | None) -> list[AppliedMark]:
    """保存した印の写しを読む。形の正しくない項目は捨てる(比較で詳細を壊さないため)。"""
    marks: list[AppliedMark] = []
    for item in value or []:
        try:
            marks.append(
                AppliedMark(
                    key=str(item["key"]),
                    label=str(item.get("label", "")),
                    stage=int(item["stage"]),
                    reason=str(item.get("reason", "")),
                    reviewed=bool(item.get("reviewed", False)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return marks


def compare(
    prediction: Prediction,
    applied: Sequence[AppliedMark],
    marked_stages: Collection[int],
    document_checks: Collection[str] | None = None,
) -> Comparison:
    """予測と反映で付いた印を突き合わせる。`marked_stages`は印を付ける段階(一度でも承認した段階)、
    `document_checks`は文書の側の確認済みのキー(None なら文書の側の確認の状態を出さない)。"""
    actual = {mark.key: mark for mark in applied}
    items: list[ComparedItem] = []
    for predicted in prediction.items:
        mark = actual.pop(predicted.key, None)
        if mark is not None:
            status: ComparisonStatus = "matched"
            reason: PredictedOnlyReason | None = None
        else:
            status = "predicted_only"
            reason = _predicted_only_reason(predicted.stage, marked_stages)
        reviewed = mark.reviewed if mark is not None else None
        if reason == "document_side" and document_checks is not None:
            reviewed = predicted.key in document_checks
        items.append(
            ComparedItem(
                key=predicted.key,
                label=predicted.label,
                stage=predicted.stage,
                status=status,
                predicted_only_reason=reason,
                predicted_reason=predicted.reason,
                actual_reason=mark.reason if mark is not None else "",
                requirement_ids=predicted.requirement_ids,
                reviewed=reviewed,
            )
        )
    for mark in actual.values():
        items.append(
            ComparedItem(
                key=mark.key,
                label=mark.label,
                stage=mark.stage,
                status="actual_only",
                predicted_only_reason=None,
                predicted_reason="",
                actual_reason=mark.reason,
                requirement_ids=(),
                reviewed=mark.reviewed,
            )
        )
    items.sort(key=lambda i: (i.stage, i.key))
    return Comparison(tuple(items))


def _predicted_only_reason(stage: int, marked_stages: Collection[int]) -> PredictedOnlyReason:
    if stage == DOCUMENT_STAGE:
        return "document_side"
    if stage not in marked_stages:
        return "unmarked_stage"
    return "not_changed"
