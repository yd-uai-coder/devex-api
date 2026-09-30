import pytest

from app.uml.domain import DiagramStatus
from app.uml.sync import DocState, diagram_sync_state


@pytest.mark.parametrize(
    ("status", "anchor_version", "expected"),
    [
        ("approved", None, "not_reflected"),
        ("exported", 1, "outdated"),
        ("approved", 2, "reflected"),
        ("exported", 2, "reflected"),
        ("reviewing", 2, "outdated"),
        ("draft", None, "not_applicable"),
    ],
)
def test_doc_state_follows_status_and_anchor_version(
    status: DiagramStatus, anchor_version: int | None, expected: DocState
) -> None:
    state = diagram_sync_state(
        status=status,
        version=2,
        source_doc_version=None,
        current_doc_version=None,
        anchor_version=anchor_version,
    )

    assert state.doc_state == expected


@pytest.mark.parametrize(
    ("source", "current", "expected"),
    [
        (3, 3, False),
        (2, 3, True),
        (3, 2, True),  # 復元で版の番号が下がった場合も古い
        (None, 3, False),  # AIで生成していない図(手で作った図)は比べない
        (3, None, False),  # 内部設計書が無い
    ],
)
def test_source_outdated_compares_versions_by_inequality(
    source: int | None, current: int | None, expected: bool
) -> None:
    state = diagram_sync_state(
        status="approved",
        version=1,
        source_doc_version=source,
        current_doc_version=current,
        anchor_version=1,
    )

    assert state.source_outdated is expected
