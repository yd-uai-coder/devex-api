"""CR の共有リンクの規則(トークン・期限・リンクの状態・前回から変わった点)のテスト。

SUT: app.change_requests の new_token / token_hash / expires_at_from / share_state /
     explanation_changes / snapshot_refs / explained_snapshots / previous_snapshot と、
     SectionChange・ExplainedSnapshot・READABLE_STATES・SHARE_STATE_LABEL・SNAPSHOT_MARKDOWN_KEY・
     DEFAULT_EXPIRES_DAYS・MAX_EXPIRES_DAYS
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため(トークンは乱数だが、形と
重ならないことだけを確かめる)
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.change_requests import (
    DEFAULT_EXPIRES_DAYS,
    MAX_EXPIRES_DAYS,
    READABLE_STATES,
    SHARE_STATE_LABEL,
    SNAPSHOT_MARKDOWN_KEY,
    ExplainedSnapshot,
    SectionChange,
    expires_at_from,
    explained_snapshots,
    explanation_changes,
    new_token,
    previous_snapshot,
    share_state,
    snapshot_refs,
    token_hash,
)

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def test_token_is_url_safe_and_unique_and_hash_is_stable() -> None:
    tokens = {new_token() for _ in range(20)}
    assert len(tokens) == 20
    token = next(iter(tokens))
    assert len(token) == 43
    assert all(c.isalnum() or c in "-_" for c in token)
    assert token_hash(token) == token_hash(token)
    assert len(token_hash(token)) == 64
    assert token_hash(token) != token


def test_expires_at_from_adds_days() -> None:
    assert DEFAULT_EXPIRES_DAYS == 14
    assert MAX_EXPIRES_DAYS == 90
    assert expires_at_from(NOW, 14) == NOW + timedelta(days=14)


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("explained", "open"),
        ("draft", "paused"),
        ("agreed", "decided"),
        ("rejected", "decided"),
        ("applied", "decided"),
        ("verified", "decided"),
        ("withdrawn", "closed"),
    ],
)
def test_share_state_follows_change_request_status(status: str, state: str) -> None:
    assert share_state(None, NOW + timedelta(days=1), status, NOW) == state


def test_share_state_revoked_wins_over_expired_and_expired_over_status() -> None:
    past = NOW - timedelta(seconds=1)
    assert share_state(NOW, past, "explained", NOW) == "revoked"
    assert share_state(None, past, "explained", NOW) == "expired"
    # 期限ちょうどは切れている
    assert share_state(None, NOW, "explained", NOW) == "expired"


def test_readable_states_and_labels() -> None:
    assert {"open", "decided"} == READABLE_STATES
    assert SHARE_STATE_LABEL["paused"] == "直しています"


OLD = """\
# 変更の説明書 CR-4: 件数の変更

- CR の状態: 説明済み

## 1. 変更の内容と背景

TOP20 にする

## 2. 対象の要件

| FR-003 | 内容の変更 |

## 3. 変更の規模

| 作業単位 | 3件 |

## 4. 影響の一覧

| トップページ(SCR-001) |
"""


def test_explanation_changes_same_markdown_is_empty() -> None:
    assert explanation_changes(OLD, OLD) == ()


def test_explanation_changes_matches_sections_by_name_across_renumbering() -> None:
    # 案を足すと「3. 選択肢」が入り、規模・影響の番号が送られる。影響の節は中身が同じなので出ない
    new = OLD.replace(
        "## 3. 変更の規模\n\n| 作業単位 | 3件 |",
        "## 3. 選択肢\n\n### 案1 一緒に\n\n## 4. 変更の規模\n\n| 作業単位 | 4件 |",
    ).replace("## 4. 影響の一覧", "## 5. 影響の一覧")
    changes = explanation_changes(OLD, new)
    assert changes == (
        SectionChange("選択肢", (), ("### 案1 一緒に",)),
        SectionChange("変更の規模", ("| 作業単位 | 3件 |",), ("| 作業単位 | 4件 |",)),
    )


def test_explanation_changes_header_and_removed_section() -> None:
    new = OLD.replace("件数の変更", "件数と矢印の変更").replace(
        "## 4. 影響の一覧\n\n| トップページ(SCR-001) |\n", ""
    )
    changes = explanation_changes(OLD, new)
    assert [c.name for c in changes] == ["題と基準", "影響の一覧"]
    assert changes[0].removed == ("# 変更の説明書 CR-4: 件数の変更",)
    assert changes[0].added == ("# 変更の説明書 CR-4: 件数と矢印の変更",)
    assert changes[1] == SectionChange("影響の一覧", ("| トップページ(SCR-001) |",), ())


def test_explained_snapshots_reads_only_explain_rows_with_snapshot() -> None:
    later = NOW + timedelta(days=1)
    refs = snapshot_refs("# v1", "f1")
    assert refs[SNAPSHOT_MARKDOWN_KEY] == "# v1"
    events = [
        ("create", None, NOW),
        ("explain", None, NOW),  # 写しの無い古い説明の行は読まない
        ("explain", refs, NOW),
        ("edit", {"fields": ["title"]}, later),
        ("explain", snapshot_refs("# v2", "f2"), later),
        ("explain", {"markdown": 1}, later),  # 形の正しくない行も読まない
    ]

    snapshots = explained_snapshots(events)

    assert snapshots == [
        ExplainedSnapshot("# v1", "f1", NOW),
        ExplainedSnapshot("# v2", "f2", later),
    ]
    assert previous_snapshot(snapshots) == ExplainedSnapshot("# v1", "f1", NOW)
    assert previous_snapshot(snapshots[:1]) is None
