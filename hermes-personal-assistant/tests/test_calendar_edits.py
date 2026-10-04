from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from assistant.tools.calendar_edits import (
    EventTimes,
    changed_event_times,
    clean_location,
    clean_title,
    describe,
    new_event_times,
    parse_local_datetime,
    time_fields,
)
from assistant.tools.calendar_service import CalendarEvent

NY = ZoneInfo("America/New_York")


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def timed_event(start, end):
    return CalendarEvent(id="e1", title="Dentist", start=start, end=end, all_day=False, location="1 Main St")


def all_day_event(first_utc, end_utc):
    return CalendarEvent(id="e2", title="Trip", start=first_utc, end=end_utc, all_day=True, location=None)


class TestCleanTitle:
    def test_collapses_whitespace(self):
        assert clean_title("  Dentist \n visit ") == "Dentist visit"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_rejects_empty(self, value):
        with pytest.raises(ValueError, match="title"):
            clean_title(value)

    def test_rejects_too_long(self):
        with pytest.raises(ValueError, match="at most 200"):
            clean_title("x" * 201)


def test_blank_location_is_none():
    assert clean_location("  ") is None


class TestParseLocalDatetime:
    def test_reads_in_user_timezone(self):
        assert parse_local_datetime("2026-10-06T15:00", NY, "start") == utc(2026, 10, 6, 19)

    def test_rejects_date_without_time(self):
        with pytest.raises(ValueError, match="needs a time"):
            parse_local_datetime("2026-10-06", NY, "start")

    def test_rejects_garbage(self):
        with pytest.raises(ValueError, match="start must be a local date and time"):
            parse_local_datetime("tuesday 3pm", NY, "start")


class TestNewEventTimes:
    def test_defaults_to_one_hour(self):
        times = new_event_times({"start": "2026-10-06T15:00"}, NY)
        assert (times.start_at, times.end_at) == (utc(2026, 10, 6, 19), utc(2026, 10, 6, 20))

    def test_uses_duration_minutes(self):
        times = new_event_times({"start": "2026-10-06T15:00", "duration_minutes": "30"}, NY)
        assert times.end_at == utc(2026, 10, 6, 19, 30)

    def test_end_wins_over_duration(self):
        times = new_event_times({"start": "2026-10-06T15:00", "end": "2026-10-06T17:00", "duration_minutes": "30"}, NY)
        assert times.end_at == utc(2026, 10, 6, 21)

    def test_requires_start(self):
        with pytest.raises(ValueError, match="start is required"):
            new_event_times({}, NY)

    def test_rejects_end_before_start(self):
        with pytest.raises(ValueError, match="after the start"):
            new_event_times({"start": "2026-10-06T15:00", "end": "2026-10-06T14:00"}, NY)

    @pytest.mark.parametrize("minutes", ["0", "-5", "abc"])
    def test_rejects_bad_duration(self, minutes):
        with pytest.raises(ValueError, match="duration_minutes"):
            new_event_times({"start": "2026-10-06T15:00", "duration_minutes": minutes}, NY)

    def test_rejects_timed_event_over_14_days(self):
        with pytest.raises(ValueError, match="14 days"):
            new_event_times({"start": "2026-10-06T15:00", "end": "2026-10-21T15:00"}, NY)

    def test_all_day_single_day(self):
        times = new_event_times({"start": "2026-10-06", "all_day": True}, NY)
        assert (times.first_day, times.last_day) == (date(2026, 10, 6), date(2026, 10, 6))

    def test_all_day_flag_as_string(self):
        assert new_event_times({"start": "2026-10-06", "all_day": "true"}, NY).all_day is True

    def test_all_day_rejects_reversed_dates(self):
        with pytest.raises(ValueError, match="on or after"):
            new_event_times({"start": "2026-10-06", "end": "2026-10-05", "all_day": True}, NY)

    def test_all_day_rejects_over_31_days(self):
        with pytest.raises(ValueError, match="31 days"):
            new_event_times({"start": "2026-10-01", "end": "2026-11-01", "all_day": True}, NY)


class TestChangedEventTimes:
    def test_no_time_args_means_no_change(self):
        assert changed_event_times(timed_event(utc(2026, 10, 6, 19), utc(2026, 10, 6, 20)), {"title": "x"}, NY) is None

    def test_moving_start_keeps_length(self):
        event = timed_event(utc(2026, 10, 6, 19), utc(2026, 10, 6, 20, 30))
        times = changed_event_times(event, {"start": "2026-10-06T16:00"}, NY)
        assert (times.start_at, times.end_at) == (utc(2026, 10, 6, 20), utc(2026, 10, 6, 21, 30))

    def test_changing_only_end(self):
        event = timed_event(utc(2026, 10, 6, 19), utc(2026, 10, 6, 20))
        times = changed_event_times(event, {"end": "2026-10-06T17:00"}, NY)
        assert (times.start_at, times.end_at) == (utc(2026, 10, 6, 19), utc(2026, 10, 6, 21))

    def test_end_before_existing_start_rejected(self):
        event = timed_event(utc(2026, 10, 6, 19), utc(2026, 10, 6, 20))
        with pytest.raises(ValueError, match="after the start"):
            changed_event_times(event, {"end": "2026-10-06T14:00"}, NY)

    def test_moving_all_day_event_keeps_span(self):
        event = all_day_event(utc(2026, 10, 6, 4), utc(2026, 10, 9, 4))  # Oct 6-8 local
        times = changed_event_times(event, {"start": "2026-10-10"}, NY)
        assert (times.first_day, times.last_day) == (date(2026, 10, 10), date(2026, 10, 12))


class TestTimeFields:
    def test_timed_uses_local_offset_and_zone(self):
        fields = time_fields(EventTimes(start_at=utc(2026, 10, 6, 19), end_at=utc(2026, 10, 6, 20)), NY)
        assert fields["start"] == {"dateTime": "2026-10-06T15:00:00-04:00", "timeZone": "America/New_York"}
        assert fields["end"]["dateTime"] == "2026-10-06T16:00:00-04:00"

    def test_all_day_end_is_exclusive(self):
        fields = time_fields(EventTimes(first_day=date(2026, 10, 6), last_day=date(2026, 10, 8)), NY)
        assert fields == {"start": {"date": "2026-10-06"}, "end": {"date": "2026-10-09"}}


class TestDescribe:
    def test_timed(self):
        result = describe("Dentist", EventTimes(start_at=utc(2026, 10, 6, 19), end_at=utc(2026, 10, 6, 20)), None, NY)
        assert (result["date"], result["start_time"], result["end_time"]) == ("Tue, Oct 6", "3:00 PM", "4:00 PM")

    def test_multi_day_all_day(self):
        result = describe("Trip", EventTimes(first_day=date(2026, 10, 6), last_day=date(2026, 10, 8)), None, NY)
        assert (result["date"], result["end_date"]) == ("Tue, Oct 6", "Thu, Oct 8")
