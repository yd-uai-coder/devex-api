"""変更要求(CR)の対象(要件 ID と変更の種類)のテスト。

SUT: app.change_requests の normalize_targets / target_ids / targets_to_json /
     targets_from_json と、種類の表(TARGET_KINDS・TARGET_KIND_LABEL)
ドライバ: pytest のテスト関数
スタブ不要 ── 対象が純粋(副作用なし)で外部依存を呼ばないため
"""

import pytest

from app.change_requests import (
    TARGET_KIND_LABEL,
    TARGET_KINDS,
    Target,
    normalize_targets,
    target_ids,
    targets_from_json,
    targets_to_json,
)


def test_kinds_have_labels() -> None:
    assert TARGET_KINDS == ("content", "delete")
    assert set(TARGET_KIND_LABEL) == set(TARGET_KINDS)
    assert Target("FR-001").kind == "content"


def test_normalize_targets_strips_and_dedupes() -> None:
    targets = [
        Target(" FR-002"),
        Target("FR-001", "delete"),
        Target("FR-002 "),
        Target("  "),
        Target("FR-001", "delete"),
    ]

    assert normalize_targets(targets) == [Target("FR-002"), Target("FR-001", "delete")]


def test_normalize_targets_rejects_conflicting_kinds() -> None:
    with pytest.raises(ValueError, match="FR-001"):
        normalize_targets([Target("FR-001"), Target("FR-001 ", "delete")])


def test_ids_and_json_round_trip() -> None:
    targets = [Target("FR-001"), Target("FR-002", "delete")]

    stored = targets_to_json(targets)

    assert stored == [
        {"requirement_id": "FR-001", "kind": "content"},
        {"requirement_id": "FR-002", "kind": "delete"},
    ]
    assert targets_from_json(stored) == targets
    assert target_ids(targets) == ["FR-001", "FR-002"]
    assert targets_from_json(None) == []
    # 知らない種類は内容の変更とみなす
    assert targets_from_json([{"requirement_id": "FR-003", "kind": "x"}]) == [Target("FR-003")]
