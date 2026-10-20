"""要素1つの合意の履歴の純粋関数のテスト。

SUT: element_history と HISTORY_EVENT_LABEL・RESTORE_REASON_LABEL(app/agreements/history.py。
     パッケージ app.agreements から)、predicted_document_keys(app/change_requests/prediction.py)
ドライバ: 各テスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.agreements import (
    HISTORY_EVENT_LABEL,
    HISTORY_EVENTS,
    RESTORE_REASON_LABEL,
    ChangeRequestRef,
    RecordFacts,
    VersionFacts,
    element_history,
)
from app.change_requests import Target, predicted_document_keys, rewritable_keys

_T = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _row(text: str, source: str = "user") -> dict:
    return {
        "id": "FR-001",
        "kind": "FR",
        "text": text,
        "priority": "Must",
        "source": source,
        "points": [],
    }


def _record(
    seq: int, content: dict | None, origin: str = "baseline", label: str = ""
) -> RecordFacts:
    return RecordFacts(
        seq=seq,
        created_at=_T + timedelta(hours=seq),
        label=label or "FR-001 予約を登録できる",
        content=content,
        origin=origin,
        origin_label={"baseline": f"B-{seq}", "adopt": "採用にする"}.get(origin, f"CR-{seq}"),
    )


def test_records_are_classified_by_content() -> None:
    """最初の合意 → 同じ中身(束の作り直し)→ 中身が変わった → 外れた → もう一度合意。"""
    records = [
        _record(1, _row("予約を登録できる")),
        _record(2, _row("予約を登録できる")),
        _record(3, _row("予約を登録・変更できる"), origin="change_request"),
        _record(4, None),
        _record(5, _row("予約を登録できる")),
    ]
    history = element_history("req:FR-001", list(reversed(records)), [], [])

    assert [e.event for e in history.entries] == [
        "agreed",
        "reaffirmed",
        "changed",
        "removed",
        "agreed",
    ]
    assert [e.origin_label for e in history.entries] == ["B-1", "B-2", "CR-3", "B-4", "B-5"]
    assert history.entries[2].event_label == HISTORY_EVENT_LABEL["changed"]
    assert history.kind == "requirement"
    assert history.current is not None and history.current["text"] == "予約を登録できる"


def test_saved_form_without_defaults_is_the_same_content() -> None:
    """保存した形の欠けた欄(既定値)では「変わった」にしない(body_of で比べる)。"""
    short = {
        "id": "FR-001",
        "kind": "FR",
        "priority": "Must",
        "text": "予約を登録できる",
        "source": "user",
    }
    history = element_history(
        "req:FR-001", [_record(1, short), _record(2, _row("予約を登録できる"))], [], []
    )
    assert [e.event for e in history.entries] == ["agreed", "reaffirmed"]


def test_removed_latest_has_no_current() -> None:
    history = element_history("req:FR-001", [_record(1, _row("a")), _record(2, None)], [], [])
    assert history.current is None
    assert history.label == "FR-001 予約を登録できる"


def test_restored_versions_are_inserted_by_time() -> None:
    """戻した版は日時の順に挟まる。同じ日時なら戻しの行が先。キーの無い版・形の悪い値は読まない。"""
    versions = [
        VersionFacts("requirements", 4, _T + timedelta(hours=1, minutes=30), [
            {"key": "req:FR-001", "reason": "changed"}
        ]),
        VersionFacts("requirements", 5, _T + timedelta(hours=2), [
            {"key": "req:FR-001", "reason": "dropped"}
        ], change_request_number=2),
        VersionFacts("requirements", 6, _T + timedelta(hours=3), [
            {"key": "req:FR-002", "reason": "changed"}
        ]),
        VersionFacts("requirements", 7, _T + timedelta(hours=3), [
            {"key": "req:FR-001", "reason": "unknown"}, "broken"  # type: ignore[list-item]
        ]),
    ]
    records = [_record(1, _row("a")), _record(2, _row("a"))]
    history = element_history("req:FR-001", records, versions, [])

    assert [(e.event, e.version) for e in history.entries] == [
        ("agreed", None),
        ("restored", 4),
        ("restored", 5),
        ("reaffirmed", None),
    ]
    restored = history.entries[2]
    assert restored.restore_reason == "dropped"
    assert restored.restore_reason_label == RESTORE_REASON_LABEL["dropped"]
    assert restored.doc_label == "要件定義書"
    assert restored.change_request_number == 2


def test_related_change_requests_by_target_and_prediction() -> None:
    """要件は対象(変更の種類つき)、画面・API は保存した予測で拾う。無関係の CR は出ない。"""
    prediction = {
        "targets": [{"requirement_id": "FR-001", "kind": "content", "text": "a"}],
        "items": [
            {
                "key": "screen:SCR-001",
                "kind": "screen",
                "stage": 0,
                "label": "SCR-001 予約",
                "path": ["req:FR-001", "screen:SCR-001"],
                "reason": "画面",
                "requirement_ids": ["FR-001"],
            }
        ],
        "unpredicted_new_requirement": False,
    }
    keys = predicted_document_keys(prediction)
    assert keys == frozenset({"screen:SCR-001"})
    assert rewritable_keys([Target("FR-001")], prediction) == {"req:FR-001", "screen:SCR-001"}
    crs = [
        ChangeRequestRef(3, "削除", "rejected", [Target("FR-001", "delete")], frozenset()),
        ChangeRequestRef(1, "変更", "applied", [Target("FR-001")], keys),
        ChangeRequestRef(2, "別", "draft", [Target("FR-002")], frozenset()),
    ]

    requirement = element_history("req:FR-001", [], [], crs)
    assert [(c.number, c.target_kind, c.predicted) for c in requirement.change_requests] == [
        (1, "content", False),
        (3, "delete", False),
    ]
    assert requirement.change_requests[1].status_label == "却下"
    assert requirement.change_requests[1].target_kind_label == "削除"
    assert requirement.label == "req:FR-001" and requirement.entries == ()

    screen = element_history("screen:SCR-001", [], [], crs)
    assert [(c.number, c.target_kind, c.predicted) for c in screen.change_requests] == [
        (1, None, True)
    ]
    assert predicted_document_keys(None) == frozenset()


def test_users_and_unknown_keys() -> None:
    users = {"users": [{"user": "店長", "scene": "予約を見る", "source": "user"}]}
    history = element_history(
        "users", [_record(1, users, label="利用者と使う場面")], [], []
    )
    assert history.kind == "users" and [e.event for e in history.entries] == ["agreed"]
    assert set(HISTORY_EVENTS) == set(HISTORY_EVENT_LABEL)
    with pytest.raises(ValueError):
        element_history("stage:3", [], [], [])
