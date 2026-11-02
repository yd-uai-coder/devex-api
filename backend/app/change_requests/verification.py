"""変更要求(CR)の検証の条件と、反映で付いた印の今の確認の状態(純粋関数)。

docs/external_design.md 2.14節「検証」。反映(CR を渡した文書の再生成)で付いた印の写し
(`change_requests.applied_impact`)を、今の段階の印(`design_stages.impact`)と突き合わせ、印ごとに
今の確認の状態を決める。印の付かない文書の側の要素(画面・API。置き場0)は、保存した予測から CR の
確認の一覧にし、人が確認済みにする(`change_requests.document_checks`)。

印の今の確認の状態:
- `open`(未確認): 今の段階に同じ要素の印があり、出どころに`cr:<番号>`を持ち、未確認。
- `reviewed`(確認済み): 同じ印があり、確認済み。
- `approved`(承認で済んだ): 上の2つ以外(印が無い・`cr:<番号>`を持たない)。段階の印は承認で消え、
  承認は他から届いた未確認の印があると止まるので、印が消えたのは確かめて承認した後である。

同じ要素に後から別の変更の印が届いても、出どころの`cr:<番号>`は残り、未確認に戻る(marks.py の
`record`)ので、確かめ直すまで検証できない。

CR で作り直した段階の承認で付く印も、印の写しに重なって同じく確かめる。作り直した段階をまだ承認して
いない間(`pending_stages`。stage_approvals.py)も、検証できない(承認で付く印が結ばれる前に検証が
済まないようにするため)。
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal, get_args

from app.change_requests.comparison import AppliedMark
from app.change_requests.prediction import Prediction
from app.detailed_design.impact import DOCUMENT_STAGE, StageImpact, cr_source

MarkConfirmation = Literal["open", "reviewed", "approved"]

MARK_CONFIRMATIONS: tuple[MarkConfirmation, ...] = get_args(MarkConfirmation)
MARK_CONFIRMATION_LABEL: dict[MarkConfirmation, str] = {
    "open": "未確認",
    "reviewed": "確認済み",
    "approved": "承認で済んだ",
}


@dataclass(frozen=True)
class MarkState:
    """反映で付いた印1つと、今の確認の状態。"""

    key: str
    label: str
    stage: int
    reason: str
    confirmation: MarkConfirmation

    @property
    def confirmed(self) -> bool:
        return self.confirmation != "open"


@dataclass(frozen=True)
class DocumentItem:
    """文書の側の確認の項目1つ(保存した予測の画面・API)と、確認済みか。"""

    key: str
    label: str
    reason: str
    reviewed: bool


@dataclass(frozen=True)
class Verification:
    """CR 1つの検証の確かめ。`marks`は置き場とキーの順、`documents`は予測の順、`pending_stages`は
    CR で作り直した後にまだ承認していない段階(段階の順)。"""

    marks: tuple[MarkState, ...]
    documents: tuple[DocumentItem, ...]
    pending_stages: tuple[int, ...] = ()

    @property
    def open_marks(self) -> int:
        return sum(1 for m in self.marks if not m.confirmed)

    @property
    def open_documents(self) -> int:
        return sum(1 for d in self.documents if not d.reviewed)

    @property
    def complete(self) -> bool:
        """印も文書の側も未確認が無く、作り直した段階がすべて承認済みか(CR の状態は見ない。
        サービスが遷移の表で確かめる)。"""
        return self.open_marks == 0 and self.open_documents == 0 and not self.pending_stages


def mark_states(
    applied: Sequence[AppliedMark], current: Mapping[int, StageImpact], number: int
) -> list[MarkState]:
    """反映で付いた印ごとに、今の段階の印(`current`=段階 → 記録)から確認の状態を決める。"""
    source = cr_source(number)
    states: list[MarkState] = []
    for mark in applied:
        impact = current.get(mark.stage)
        now = next((m for m in impact.marks if m.key == mark.key), None) if impact else None
        if now is None or source not in now.sources:
            confirmation: MarkConfirmation = "approved"
        else:
            confirmation = "reviewed" if now.reviewed else "open"
        states.append(MarkState(mark.key, mark.label, mark.stage, mark.reason, confirmation))
    return sorted(states, key=lambda s: (s.stage, s.key))


def document_items(prediction: Prediction, checked: Iterable[str]) -> list[DocumentItem]:
    """保存した予測の文書の側の要素(置き場0)を、確認の一覧にする(予測の順)。"""
    done = set(checked)
    return [
        DocumentItem(item.key, item.label, item.reason, item.key in done)
        for item in prediction.items
        if item.stage == DOCUMENT_STAGE
    ]


def checked_documents(
    existing: Sequence[str] | None, keys: Iterable[str], reviewed: bool, known: Iterable[str]
) -> list[str]:
    """文書の側の確認済みのキーを更新する。知らないキー(予測に無い)は無視する(並びは`known`の順)。"""
    order = list(known)
    done = set(existing or []) & set(order)
    wanted = set(keys) & set(order)
    done = done | wanted if reviewed else done - wanted
    return [key for key in order if key in done]


def verification_of(
    applied: Sequence[AppliedMark],
    current: Mapping[int, StageImpact],
    number: int,
    prediction: Prediction,
    checked: Iterable[str],
    pending: Iterable[int] = (),
) -> Verification:
    """反映で付いた印と文書の側の確認、まだ承認していない作り直した段階(`pending`)から、検証の
    確かめを作る。"""
    return Verification(
        tuple(mark_states(applied, current, number)),
        tuple(document_items(prediction, checked)),
        tuple(sorted(pending)),
    )


def with_confirmations(
    applied: Sequence[AppliedMark], states: Sequence[MarkState]
) -> list[AppliedMark]:
    """写しの印の`reviewed`を、今の確認の状態(未確認以外は確認済み)に置き換える(比較に使う)。"""
    confirmed = {s.key: s.confirmed for s in states}
    return [replace(m, reviewed=confirmed.get(m.key, m.reviewed)) for m in applied]
