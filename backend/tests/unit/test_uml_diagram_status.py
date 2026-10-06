import pytest

from app.uml.domain import (
    DIAGRAM_STATUSES,
    STATUS_AFTER_APPROVE,
    STATUS_AFTER_EDIT,
    STATUS_AFTER_EXPORT,
    can_approve,
    can_export,
    parse_status,
)


def test_statuses_follow_the_review_order() -> None:
    assert DIAGRAM_STATUSES == ("draft", "reviewing", "approved", "exported")


def test_parse_status_accepts_known_values_and_rejects_unknown() -> None:
    assert parse_status("approved") == "approved"
    with pytest.raises(ValueError):
        parse_status("archived")


@pytest.mark.parametrize(
    ("status", "approvable", "exportable"),
    [
        ("draft", True, False),
        ("reviewing", True, False),
        ("approved", False, True),
        ("exported", False, True),
    ],
)
def test_approve_and_export_are_allowed_only_from_their_states(
    status: str, approvable: bool, exportable: bool
) -> None:
    parsed = parse_status(status)
    assert can_approve(parsed) is approvable
    assert can_export(parsed) is exportable


def test_transition_targets() -> None:
    assert STATUS_AFTER_EDIT == "reviewing"
    assert STATUS_AFTER_APPROVE == "approved"
    assert STATUS_AFTER_EXPORT == "exported"
