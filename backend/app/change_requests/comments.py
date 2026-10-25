"""CR のコメント欄の規則(並べ方・返信できるコメント。純粋関数)。

docs/external_design.md 2.16節「共有リンク」。コメント欄は CR に1本で、クライアントが共有リンクから
名前を書いてコメントし、開発者がそのコメントに返信する(1段。返信への返信と、開発者からの
新しいコメントは無い)。リンクを作り直してもコメントは CR に残る。各コメントには、書いたときに
見ていた説明書の版(指紋)を残す。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

COMMENT_BODY_MAX = 2000
# クライアントに見せる、開発者の返信の書き手の名前(利用者の名前は出さない)
DEVELOPER_LABEL = "開発者"


@dataclass(frozen=True)
class CommentFacts:
    """コメント1つの値(DB の行から写したもの)。`author_name`はクライアントが書いた名前
    (開発者の返信は空)。`parent_id`は返信の親(コメントは None)。`author_id`は返信した利用者
    (開発者の画面だけで使う)。"""

    id: Any
    parent_id: Any
    author_name: str
    is_developer: bool
    body: str
    fingerprint: str
    created_at: datetime
    author_id: Any = None


@dataclass(frozen=True)
class CommentThread:
    """コメント1つと、それへの返信(古い順)。"""

    comment: CommentFacts
    replies: tuple[CommentFacts, ...]


def comment_threads(comments: Iterable[CommentFacts]) -> list[CommentThread]:
    """コメントを、親のコメントごとに返信をまとめて並べる(どちらも古い順)。親の無い返信は出さない。"""
    ordered = sorted(comments, key=lambda c: c.created_at)
    replies: dict[Any, list[CommentFacts]] = {}
    for comment in ordered:
        if comment.parent_id is not None:
            replies.setdefault(comment.parent_id, []).append(comment)
    return [
        CommentThread(comment, tuple(replies.get(comment.id, [])))
        for comment in ordered
        if comment.parent_id is None
    ]


def can_reply(parent: CommentFacts) -> bool:
    """`parent`に返信できるか(クライアントのコメントだけ。返信には返信しない)。"""
    return parent.parent_id is None and not parent.is_developer
