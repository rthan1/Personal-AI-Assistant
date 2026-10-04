from datetime import datetime, timezone

import pytest

from assistant.tools.clock import get_current_time


def fixed(dt: datetime):
    return lambda: dt


def test_converts_utc_to_local_during_dst():
    result = get_current_time("America/New_York", now=fixed(datetime(2026, 10, 3, 21, 14, tzinfo=timezone.utc)))

    assert result["iso"] == "2026-10-03T17:14:00-04:00"
    assert result["time"] == "5:14 PM"
    assert result["date"] == "Saturday, October 3, 2026"
    assert result["utc_offset"] == "-0400"


def test_uses_standard_time_in_winter():
    result = get_current_time("America/New_York", now=fixed(datetime(2026, 1, 15, 14, 5, tzinfo=timezone.utc)))

    assert result["iso"] == "2026-01-15T09:05:00-05:00"
    assert result["time"] == "9:05 AM"


def test_local_date_can_differ_from_utc_date():
    result = get_current_time("America/Los_Angeles", now=fixed(datetime(2026, 10, 4, 2, 30, tzinfo=timezone.utc)))

    assert result["date"] == "Saturday, October 3, 2026"
    assert result["time"] == "7:30 PM"


def test_unknown_timezone_raises():
    with pytest.raises(ValueError, match="Unknown timezone"):
        get_current_time("Mars/Olympus_Mons")
