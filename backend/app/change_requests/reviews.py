"""変更要求(CR)に設計レビューの回を結ぶ条件(純粋関数。docs/external_design.md 2.14節「検証」)。

レビューは任意で、反映済みの間に、反映(最初の`apply`)の後に流して成功した回から開発者が選んで
結ぶ(複数可)。反映の前の回は、CR の変更をレビューしていないので結ばない。
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from app.change_requests.states import CHECKABLE_STATUSES

# 結べる回の状態
LINKABLE_RUN_STATUS = "succeeded"


def aware(value: datetime) -> datetime:
    """タイムゾーンの無い日時(SQLite が返す)を UTC として扱う。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def first_applied_at(events: Iterable[tuple[str, datetime]]) -> datetime | None:
    """時系列(操作, 日時)の最初の反映(`apply`)の日時。反映していなければ None。"""
    return next((aware(at) for action, at in events if action == "apply"), None)


def review_link_problem(
    status: str,
    applied_at: datetime | None,
    run_status: str,
    run_started_at: datetime,
    *,
    already_linked: bool,
) -> str | None:
    """回を CR に結べない理由(結べれば None)。"""
    if status not in CHECKABLE_STATUSES or applied_at is None:
        return "反映済みの CR にだけ結べます。"
    if already_linked:
        return "このレビューはもう結んであります。"
    if run_status != LINKABLE_RUN_STATUS:
        return "成功したレビューだけを結べます。"
    if aware(run_started_at) < applied_at:
        return "反映の後に流したレビューだけを結べます。"
    return None

