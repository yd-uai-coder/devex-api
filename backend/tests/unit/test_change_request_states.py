"""変更要求(CR)の状態と遷移の表のテスト。

SUT: app.change_requests(next_status / allowed_actions / overlaps と、
     状態・ラベルの表・再生成に渡せる状態)
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

import pytest

from app.change_requests import (
    ACTIVE_STATUSES,
    BACKGROUND_SOURCE_LABEL,
    BACKGROUND_SOURCES,
    DOCUMENT_REFLECTABLE_STATUSES,
    STAGE_REFLECTABLE_STATUSES,
    STATUS_LABEL,
    STATUSES,
    TRANSITIONS,
    Overlap,
    allowed_actions,
    next_status,
    overlaps,
)


@pytest.mark.parametrize(
    ("current", "action", "expected"),
    [
        ("draft", "explain", "explained"),
        ("explained", "agree", "agreed"),
        ("explained", "reject", "rejected"),
        ("agreed", "apply", "applied"),
        ("applied", "apply", "applied"),  # 反映のやり直し
        ("applied", "verify", "verified"),
        ("draft", "withdraw", "withdrawn"),
        ("explained", "withdraw", "withdrawn"),
        ("draft", "edit", "draft"),
        ("explained", "edit", "draft"),  # 説明した中身と変わるので下書きに戻る
    ],
)
def test_allowed_transitions(current: str, action: str, expected: str) -> None:
    assert next_status(current, action) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("current", "action"),
    [
        ("draft", "agree"),  # 説明の前に合意しない
        ("agreed", "withdraw"),  # 取り下げは合意の前だけ
        ("agreed", "edit"),  # 合意の後は直さない
        ("rejected", "explain"),  # 却下は写して出し直す
        ("rejected", "edit"),
        ("withdrawn", "explain"),
        ("verified", "apply"),
        ("explained", "apply"),  # 合意の前は反映しない
        ("draft", "copy"),  # 写しは遷移の表に無い
    ],
)
def test_rejected_transitions(current: str, action: str) -> None:
    assert next_status(current, action) is None  # type: ignore[arg-type]


def test_allowed_actions_lists_copy_for_rejected_only() -> None:
    assert allowed_actions("draft") == ["edit", "explain", "withdraw"]
    assert allowed_actions("explained") == ["edit", "agree", "reject", "withdraw"]
    assert allowed_actions("rejected") == ["copy"]
    assert allowed_actions("withdrawn") == []
    assert allowed_actions("verified") == []
    assert allowed_actions("applied") == ["apply", "verify"]


def test_reflectable_statuses() -> None:
    # 段階は、文書へ反映した後(反映済み)だけ
    assert set(DOCUMENT_REFLECTABLE_STATUSES) == {"agreed", "applied"}
    assert set(STAGE_REFLECTABLE_STATUSES) == {"applied"}


def test_tables_cover_every_status_and_source() -> None:
    assert set(STATUS_LABEL) == set(STATUSES)
    assert set(BACKGROUND_SOURCE_LABEL) == set(BACKGROUND_SOURCES)
    assert set(ACTIVE_STATUSES) == {"draft", "explained", "agreed", "applied"}
    for table in TRANSITIONS.values():
        assert set(table) <= set(STATUSES)
        assert set(table.values()) <= set(STATUSES)


def test_overlaps_keeps_order_and_skips_disjoint() -> None:
    found = overlaps(
        ["FR-005", "FR-001"],
        [(1, ["FR-001"]), (2, ["NFR-001"]), (3, ["FR-001", "FR-005"])],
    )

    assert found == [
        Overlap(number=1, requirement_ids=["FR-001"]),
        Overlap(number=3, requirement_ids=["FR-005", "FR-001"]),
    ]
    assert overlaps([], [(1, ["FR-001"])]) == []

