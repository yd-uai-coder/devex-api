"""CR のコメント欄の規則(並べ方・返信できるコメント)のテスト。

SUT: app.change_requests の comment_threads / can_reply と、CommentFacts・CommentThread・
     COMMENT_BODY_MAX・DEVELOPER_LABEL
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from datetime import UTC, datetime, timedelta

from app.change_requests import (
    COMMENT_BODY_MAX,
    DEVELOPER_LABEL,
    CommentFacts,
    CommentThread,
    can_reply,
    comment_threads,
)

_AT = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def _comment(
    id_: int, minutes: int, *, parent: int | None = None, developer: bool = False
) -> CommentFacts:
    return CommentFacts(
        id=id_,
        parent_id=parent,
        author_name="" if developer else "山田",
        is_developer=developer,
        body=f"本文{id_}",
        fingerprint="f" * 64,
        created_at=_AT + timedelta(minutes=minutes),
    )


def test_threads_group_replies_under_parent_in_time_order() -> None:
    """返信は親の下に古い順でまとめ、コメントも古い順に並べる(渡した順によらない)。"""
    first = _comment(1, 0)
    second = _comment(2, 10)
    reply_late = _comment(3, 30, parent=1, developer=True)
    reply_early = _comment(4, 5, parent=1, developer=True)

    threads = comment_threads([reply_late, second, reply_early, first])

    assert threads == [
        CommentThread(first, (reply_early, reply_late)),
        CommentThread(second, ()),
    ]


def test_threads_drop_orphan_replies() -> None:
    """親の無い返信は出さない(親は CR と一緒に消えるので、通常は起きない)。"""
    assert comment_threads([_comment(5, 0, parent=99, developer=True)]) == []


def test_can_reply_only_to_client_comment() -> None:
    """返信できるのはクライアントのコメントだけ(返信への返信・開発者の行には返信しない)。"""
    client = _comment(1, 0)
    reply = _comment(2, 1, parent=1, developer=True)

    assert can_reply(client) is True
    assert can_reply(reply) is False
    assert can_reply(_comment(3, 2, developer=True)) is False


def test_constants() -> None:
    assert COMMENT_BODY_MAX == 2000
    assert DEVELOPER_LABEL == "開発者"
