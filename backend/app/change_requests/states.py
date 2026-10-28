"""変更要求(CR)の状態と遷移の表(純粋関数。docs/external_design.md 2.14節)。

状態: 下書き → 説明済み → 合意 / 却下 → 反映済み → 検証済み。合意の前(下書き・説明済み)なら
取り下げ。説明済みで中身を直すと下書きに戻る(説明した中身と変わるため)。却下は終わりの状態で、
出し直すときは写して新しい CR(下書き)を作る(元の CR は却下と理由のまま残す)。
反映(apply)は、CR を渡した再生成が終わったときに記録する(反映済みの間もやり直せ、そのたびに
時系列に1行足す)。検証(verify)は、反映で付いた印と文書の側の確認がすべて済んだときだけ
(条件は verification.py)。レビューを結ぶ(link_review)・束に入れる(baseline)・CR で作り直した段階の
承認で印を結ぶ(approve_stage。stage_approvals.py)と、共有リンクの作成(share。下書きの CR では
説明済みにする explain の行になる)・取り消し(unshare)・期限の延長(extend_share)と、コメント欄の
クライアントのコメント(comment)・開発者の返信(reply_comment)と、重なる CR の競合を確かめた
(check_conflict。conflicts.py)は、状態を変えない記録だけの行
(写しの copied と同じく、遷移の表には持たない。sharing.py・comments.py)。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, get_args

ChangeRequestStatus = Literal[
    "draft", "explained", "agreed", "rejected", "applied", "verified", "withdrawn"
]
ChangeRequestAction = Literal[
    "create",
    "copy",
    "copied",
    "edit",
    "explain",
    "agree",
    "reject",
    "withdraw",
    "apply",
    "verify",
    "link_review",
    "baseline",
    "approve_stage",
    "share",
    "unshare",
    "extend_share",
    "comment",
    "reply_comment",
    "check_conflict",
]
BackgroundSource = Literal["meeting", "email", "document", "other"]

STATUSES: tuple[ChangeRequestStatus, ...] = get_args(ChangeRequestStatus)
BACKGROUND_SOURCES: tuple[BackgroundSource, ...] = get_args(BackgroundSource)

STATUS_LABEL: dict[ChangeRequestStatus, str] = {
    "draft": "下書き",
    "explained": "説明済み",
    "agreed": "合意",
    "rejected": "却下",
    "applied": "反映済み",
    "verified": "検証済み",
    "withdrawn": "取り下げ",
}
BACKGROUND_SOURCE_LABEL: dict[BackgroundSource, str] = {
    "meeting": "打ち合わせ",
    "email": "メール",
    "document": "資料",
    "other": "その他",
}

# 新しく作った CR の状態(作成・写しとも)
INITIAL_STATUS: ChangeRequestStatus = "draft"

# 操作 → {元の状態: 後の状態}。表に無い組は不正な遷移
TRANSITIONS: dict[ChangeRequestAction, dict[ChangeRequestStatus, ChangeRequestStatus]] = {
    "edit": {"draft": "draft", "explained": "draft"},
    "explain": {"draft": "explained"},
    "agree": {"explained": "agreed"},
    "reject": {"explained": "rejected"},
    "withdraw": {"draft": "withdrawn", "explained": "withdrawn"},
    "apply": {"agreed": "applied", "applied": "applied"},
    "verify": {"applied": "verified"},
}

# 写して新しい下書きを作れる状態
COPYABLE_STATUSES: frozenset[ChangeRequestStatus] = frozenset({"rejected"})
# 進行中(重なりを示す対象)。終わった CR(却下・検証済み・取り下げ)は数えない
ACTIVE_STATUSES: frozenset[ChangeRequestStatus] = frozenset(
    {"draft", "explained", "agreed", "applied"}
)
# 合意の前(判断していない)。説明書の規模に、束の後に反映したほかの CR の注記を出す状態
UNDECIDED_STATUSES: frozenset[ChangeRequestStatus] = frozenset({"draft", "explained"})
# 再生成に渡せる状態(文書は合意・反映済み、段階は文書へ反映した後の反映済みだけ)
DOCUMENT_REFLECTABLE_STATUSES: frozenset[ChangeRequestStatus] = frozenset({"agreed", "applied"})
STAGE_REFLECTABLE_STATUSES: frozenset[ChangeRequestStatus] = frozenset({"applied"})
# 反映の後の確かめ(文書の側の確認・レビューを結ぶ)ができる状態
CHECKABLE_STATUSES: frozenset[ChangeRequestStatus] = frozenset({"applied"})
# 新しい合意済みの版(束)に入れられる状態
INCLUDABLE_STATUSES: frozenset[ChangeRequestStatus] = frozenset({"verified"})
# 判断(合意・却下)の操作
DECISION_ACTIONS: dict[str, ChangeRequestAction] = {"agreed": "agree", "rejected": "reject"}


def next_status(current: str, action: ChangeRequestAction) -> ChangeRequestStatus | None:
    """`current`の CR に`action`をした後の状態。許されない遷移なら None。"""
    return TRANSITIONS.get(action, {}).get(current)  # type: ignore[call-overload]


def allowed_actions(current: str) -> list[ChangeRequestAction]:
    """`current`の CR にできる操作(表の順。写しを含む)。画面のボタンの出し分けに使う。"""
    actions: list[ChangeRequestAction] = [
        action for action, table in TRANSITIONS.items() if current in table
    ]
    if current in COPYABLE_STATUSES:
        actions.append("copy")
    return actions


@dataclass(frozen=True)
class Overlap:
    """同じ要件を対象にする、ほかの進行中の CR(番号)と、重なった要件 ID(対象の順)。"""

    number: int
    requirement_ids: list[str]


def overlaps(
    targets: Sequence[str], others: Iterable[tuple[int, Sequence[str]]]
) -> list[Overlap]:
    """`targets`と重なる、ほかの CR(番号, 対象)。重なりの無い CR は入れない(渡した順)。"""
    found = []
    for number, other_targets in others:
        shared = [req_id for req_id in targets if req_id in set(other_targets)]
        if shared:
            found.append(Overlap(number=number, requirement_ids=shared))
    return found

