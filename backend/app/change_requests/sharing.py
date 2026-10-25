"""CR の共有リンクの規則(トークン・期限・リンクの状態・前回から変わった点。純粋関数)。

docs/external_design.md 2.16節「共有リンク」。開発者が CR ごとにリンクを作り、クライアントは
アカウントを作らずに、その CR の説明書・選択肢・規模を読む。

- トークンは推測できない乱数(`secrets.token_urlsafe`。256 bit)。DB にはその sha256 だけを残し、
  トークンそのものは作った応答で1度だけ返す(なくしたら作り直す)。
- 期限は作るときに日数で決め(既定14日・上限90日)、延長は今から N 日に置き直す。
- リンクの状態は保存せず、取り消し・期限と CR の状態から読むときに決める。取り消し > 期限切れ >
  CR の状態(説明済み = 開いている、下書き = 直している〈止まっている〉、合意・却下の後 = 判断済み、
  取り下げ = 閉じた)。下書きに戻ってもリンクは取り消さないので、説明済みに戻れば同じリンクで
  再開する。
- 前回から変わった点は、前回説明済みにした時点の説明書の md と今の md を、節(`## ` の見出し)ごとに
  比べる。節は見出しの番号でなく名前で対応づける(案を足すと「3. 選択肢」が入り、後ろの節の番号が
  1つずつ送られるため)。前回の写しは、説明済みにした時系列の行(`explain`)の`refs`に残す
  (`markdown`・`fingerprint`。md の写しは CR の詳細の応答には出さない)。
"""

import difflib
import hashlib
import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

ShareState = Literal["open", "paused", "decided", "closed", "expired", "revoked"]

SHARE_STATE_LABEL: dict[ShareState, str] = {
    "open": "開いている",
    "paused": "直しています",
    "decided": "判断済み",
    "closed": "閉じた",
    "expired": "期限切れ",
    "revoked": "取り消し",
}
# CR の状態 → リンクの状態(取り消し・期限切れでないとき)
_STATE_BY_STATUS: dict[str, ShareState] = {
    "explained": "open",
    "draft": "paused",
    "agreed": "decided",
    "rejected": "decided",
    "applied": "decided",
    "verified": "decided",
    "withdrawn": "closed",
}
# リンクの状態のうち、説明書を見せるもの(直している・閉じたリンクは中身を見せない)
READABLE_STATES: frozenset[ShareState] = frozenset({"open", "decided"})

DEFAULT_EXPIRES_DAYS = 14
MAX_EXPIRES_DAYS = 90
# トークンの乱数のバイト数(token_urlsafe で 43 文字)
TOKEN_BYTES = 32
# 説明書の頭(最初の節の見出しより前。題・基準の版・CR の状態)の名前
HEADER_SECTION = "題と基準"

_SECTION_HEADING = re.compile(r"^## (?:\d+\. )?(?P<name>.+)$")


def new_token() -> str:
    """推測できないトークン(URL にそのまま載る文字だけ)。"""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    """トークンの sha256(DB に残す値。引くときも同じ関数で求める)。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def expires_at_from(now: datetime, days: int) -> datetime:
    """今から`days`日後の期限。"""
    return now + timedelta(days=days)


def share_state(
    revoked_at: datetime | None, expires_at: datetime, cr_status: str, now: datetime
) -> ShareState:
    """リンクの今の状態(取り消し > 期限切れ > CR の状態)。"""
    if revoked_at is not None:
        return "revoked"
    if expires_at <= now:
        return "expired"
    return _STATE_BY_STATUS.get(cr_status, "closed")


@dataclass(frozen=True)
class SectionChange:
    """前回から変わった節1つ。`name`は節の名前(番号を除いた見出し)、`removed`・`added`は
    消えた行と足した行(空行を除く。出た順)。節ごと消えた・足したときは、その節の全行。"""

    name: str
    removed: tuple[str, ...]
    added: tuple[str, ...]


def split_sections(markdown: str) -> list[tuple[str, list[str]]]:
    """説明書の md を(節の名前, 節の行)に分ける(見出しの行は含めない。最初の節の前は
    `HEADER_SECTION`)。"""
    sections: list[tuple[str, list[str]]] = [(HEADER_SECTION, [])]
    for line in markdown.splitlines():
        match = _SECTION_HEADING.match(line)
        if match:
            sections.append((match.group("name").strip(), []))
        else:
            sections[-1][1].append(line)
    return sections


def explanation_changes(old_markdown: str, new_markdown: str) -> tuple[SectionChange, ...]:
    """前回の説明書から今の説明書で変わった節(今の説明書の節の順。前回だけにある節は後ろ)。
    同じなら空。"""
    old = dict(split_sections(old_markdown))
    new = split_sections(new_markdown)
    changes = []
    for name, lines in new:
        removed, added = _diff(old.get(name, []), lines)
        if removed or added:
            changes.append(SectionChange(name, removed, added))
    new_names = {name for name, _ in new}
    for name, lines in old.items():
        if name not in new_names:
            removed, _ = _diff(lines, [])
            if removed:
                changes.append(SectionChange(name, removed, ()))
    return tuple(changes)


def _diff(old: Sequence[str], new: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """2つの行の並びの、消えた行と足した行(空行を除く)。"""
    removed: list[str] = []
    added: list[str] = []
    matcher = difflib.SequenceMatcher(a=list(old), b=list(new), autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(line for line in old[i1:i2] if line.strip())
        if tag in ("replace", "insert"):
            added.extend(line for line in new[j1:j2] if line.strip())
    return tuple(removed), tuple(added)


# 説明済みにした行の`refs`に残す説明書の写しのキー
SNAPSHOT_MARKDOWN_KEY = "markdown"
SNAPSHOT_FINGERPRINT_KEY = "fingerprint"
EXPLAIN_ACTION = "explain"


def snapshot_refs(markdown: str, fingerprint: str) -> dict[str, str]:
    """説明済みにした行の`refs`(その時点の説明書の md と指紋)。"""
    return {SNAPSHOT_MARKDOWN_KEY: markdown, SNAPSHOT_FINGERPRINT_KEY: fingerprint}


@dataclass(frozen=True)
class ExplainedSnapshot:
    """説明済みにした時点の説明書の写し(時系列の行から読んだもの)。"""

    markdown: str
    fingerprint: str
    explained_at: datetime


def explained_snapshots(
    events: Iterable[tuple[str, Mapping[str, Any] | None, datetime]],
) -> list[ExplainedSnapshot]:
    """時系列(操作, refs, 日時)の、説明済みにした行の写し(起きた順)。写しの無い行(この仕組みより
    前に説明済みにした行)は読まない。"""
    snapshots = []
    for action, refs, at in events:
        if action != EXPLAIN_ACTION or not refs:
            continue
        markdown = refs.get(SNAPSHOT_MARKDOWN_KEY)
        fingerprint = refs.get(SNAPSHOT_FINGERPRINT_KEY)
        if isinstance(markdown, str) and isinstance(fingerprint, str):
            snapshots.append(ExplainedSnapshot(markdown, fingerprint, at))
    return snapshots


def previous_snapshot(snapshots: Sequence[ExplainedSnapshot]) -> ExplainedSnapshot | None:
    """今の説明の前回の写し(説明済みにした行が2つ以上あるときの、最後から2つ目)。"""
    return snapshots[-2] if len(snapshots) >= 2 else None


# リンクの状態のうち、書き込み(コメント・判断)を受けるもの。直している間は、見た説明書と合意する
# 中身がずれるので受けない
WRITABLE_STATES: frozenset[ShareState] = frozenset({"open"})

WriteProblem = Literal["not_writable", "changed"]

DecisionSource = Literal["developer", "share_link"]
DECISION_SOURCE_LABEL: dict[DecisionSource, str] = {
    "developer": "開発者が記録",
    "share_link": "共有リンク",
}
# 共有リンクで理由を書かずに合意したときの、記録の理由
SHARE_AGREE_REASON = "共有リンクで合意"


def write_problem(
    state: ShareState, seen_fingerprint: str, current_fingerprint: str
) -> WriteProblem | None:
    """共有リンクから書き込めない理由。リンクが開いていない(`not_writable`)か、クライアントが見た
    説明書の版が今の版と違う(`changed`。読み直してもらう)。書き込めるなら None。"""
    if state not in WRITABLE_STATES:
        return "not_writable"
    if seen_fingerprint != current_fingerprint:
        return "changed"
    return None


def share_decision_reason(decision: str, reason: str) -> str | None:
    """共有リンクの判断の、記録する理由。却下は理由が要る(出し直しの材料。空なら None)。合意は
    任意で、空なら`SHARE_AGREE_REASON`。"""
    text = reason.strip()
    if text:
        return text
    return None if decision == "rejected" else SHARE_AGREE_REASON


def decision_source_label(source: str | None) -> str | None:
    """判断の出どころの表示名(出どころを残す前の判断・知らない値は None)。"""
    return DECISION_SOURCE_LABEL.get(source)  # type: ignore[call-overload]
