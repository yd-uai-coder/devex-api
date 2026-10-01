from datetime import UTC, datetime, timedelta

from app.services.generation_staleness import STALE_GENERATION_AFTER, is_stale

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_threshold_is_fifteen_minutes() -> None:
    assert timedelta(minutes=15) == STALE_GENERATION_AFTER


def test_generation_within_threshold_is_not_stale() -> None:
    assert is_stale(NOW - timedelta(minutes=15), NOW) is False


def test_generation_beyond_threshold_is_stale() -> None:
    assert is_stale(NOW - timedelta(minutes=15, seconds=1), NOW) is True


def test_naive_timestamp_is_treated_as_utc() -> None:
    """SQLiteはタイムゾーンの無い時刻を返すため、UTCとみなして比べる。"""
    naive = (NOW - timedelta(minutes=20)).replace(tzinfo=None)

    assert is_stale(naive, NOW) is True
