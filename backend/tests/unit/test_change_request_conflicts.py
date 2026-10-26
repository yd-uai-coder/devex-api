"""重なる CR の競合(純粋関数)のテスト。

SUT: conflicts・base_seq・changed_after・is_checked・unchecked・checks_for
     (app/change_requests/conflicts.py)
ドライバ: 各テスト関数
スタブ: スタブ不要 ── 対象が純粋(副作用なし)で、合意の記録は RecordFact で組むため
"""

from app.change_requests import (
    RecordFact,
    base_seq,
    changed_after,
    checks_for,
    conflicts,
    is_checked,
    unchecked,
)

B1 = "b1"
B2 = "b2"


def _baseline(seq: int, key: str, text: str, baseline_id: str = B1) -> RecordFact:
    return RecordFact(seq, key, "baseline", baseline_id, None, {"id": key[4:], "text": text})


def _cr(seq: int, key: str, number: int, text: str | None) -> RecordFact:
    content = {"id": key[4:], "text": text} if text is not None else None
    return RecordFact(seq, key, "change_request", None, number, content)


RECORDS = [
    _baseline(1, "req:FR-001", "予約を登録する"),
    _baseline(2, "req:FR-002", "予約を消す"),
    _cr(3, "req:FR-001", 1, "予約を登録し、メールを送る"),
    _cr(4, "req:FR-002", 3, None),  # CR-3 が消した
    _cr(5, "req:FR-001", 1, "予約を登録し、メールとSMSを送る"),  # CR-1 のやり直し
]


def test_other_cr_change_after_base_bundle_is_a_conflict() -> None:
    [found] = conflicts(["FR-001"], RECORDS, B1, own_number=2)

    assert (found.key, found.requirement_id, found.by_numbers) == ("req:FR-001", "FR-001", (1,))
    assert found.base_text == "予約を登録する"
    assert (found.current_text, found.latest_seq) == ("予約を登録し、メールとSMSを送る", 5)


def test_removed_by_other_cr_has_no_current_text() -> None:
    [found] = conflicts(["FR-002"], RECORDS, B1, own_number=2)

    assert (found.by_numbers, found.current_text) == ((3,), None)


def test_own_changes_and_untouched_targets_are_not_conflicts() -> None:
    assert conflicts(["FR-001"], RECORDS, B1, own_number=1) == []
    assert conflicts(["FR-099"], RECORDS, B1, own_number=2) == []


def test_changes_before_a_newer_base_bundle_are_not_conflicts() -> None:
    """CR-1 の変更を入れた新しい束 B-2 を基準にした CR は、CR-1 と競合しない。"""
    records = [*RECORDS, _baseline(6, "req:FR-001", "予約を登録し、メールとSMSを送る", B2)]

    assert conflicts(["FR-001"], records, B2, own_number=2) == []


def test_no_records_of_base_bundle_means_no_conflicts() -> None:
    assert base_seq(RECORDS, "missing") is None
    assert conflicts(["FR-001"], RECORDS, "missing", own_number=2) == []


def test_changed_after_groups_cr_records_by_key() -> None:
    changed = changed_after(RECORDS, 2, exclude_number=3)

    assert {key: [r.seq for r in rs] for key, rs in changed.items()} == {"req:FR-001": [3, 5]}


def test_checks_hold_until_another_change() -> None:
    found = conflicts(["FR-001", "FR-002"], RECORDS, B1, own_number=2)
    checks = checks_for(found)

    assert checks == [{"key": "req:FR-001", "seq": 5}, {"key": "req:FR-002", "seq": 4}]
    assert unchecked(found, checks) == []
    # 確かめた後に CR-1 がさらに変えると、FR-001 の印は効かない
    later = conflicts(["FR-001", "FR-002"], [*RECORDS, _cr(7, "req:FR-001", 1, "c")], B1, 2)
    assert [c.requirement_id for c in unchecked(later, checks)] == ["FR-001"]
    assert is_checked(later[1], checks)


def test_records_with_unchanged_content_are_not_changes() -> None:
    """文を変えなかった反映(前の記録と同じ中身の行)は競合にせず、確かめの印も外さない。"""
    same = [
        _baseline(1, "req:FR-001", "予約を登録する"),
        _cr(2, "req:FR-001", 1, "予約を登録する"),
    ]
    assert conflicts(["FR-001"], same, B1, own_number=2) == []

    changed = [*same, _cr(3, "req:FR-001", 1, "予約を登録し、メールを送る")]
    [found] = conflicts(["FR-001"], changed, B1, own_number=2)
    checks = checks_for([found])
    redo = [*changed, _cr(4, "req:FR-001", 1, "予約を登録し、メールを送る")]
    assert unchecked(conflicts(["FR-001"], redo, B1, own_number=2), checks) == []
