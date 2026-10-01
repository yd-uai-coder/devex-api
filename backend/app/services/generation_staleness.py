"""AI生成が「生成中」のまま止まったかどうかの判定(純粋関数)。

生成はバックグラウンドタスクで動くため、途中でプロセスが再起動したり、結果の保存で例外が出たり
すると、「生成中」の印(`projects.status='generating'`・`uml_diagrams.generation_status=
'generating'`)だけが残る。印が残ると、二重実行の防止(409)によって以後の生成がすべて塞がる。

そこで、生成を始めてからしきい値を超えても「生成中」のものは止まったとみなし、失敗に戻す。
しきい値は15分にした(Phase 15 で決定)。UML図の生成は1回で最大5対象を直列に処理し、1対象あたり
再試行込みで1〜2分かかるため、正常な生成を誤って止めない長さを取った。
"""

from datetime import UTC, datetime, timedelta

STALE_GENERATION_AFTER = timedelta(minutes=15)


def is_stale(
    started_at: datetime, now: datetime, *, after: timedelta = STALE_GENERATION_AFTER
) -> bool:
    """`started_at`(生成を始めた時刻)から`after`を超えて経ったか。タイムゾーンの無い時刻
    (SQLiteが返す値)はUTCとみなす。"""
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now - started_at > after
