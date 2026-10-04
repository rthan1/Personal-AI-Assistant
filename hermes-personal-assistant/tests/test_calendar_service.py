from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from assistant.tools.calendar_service import (
    Attendee,
    GoogleCalendarSource,
    day_bounds_utc,
    format_event,
    get_event,
    get_events,
    is_online_location,
    parse_event,
    resolve_date_range,
)

NY = ZoneInfo("America/New_York")


class FakeSource:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def list_events(self, time_min, time_max, page_token):
        self.calls.append((time_min, time_max, page_token))
        return self.pages[len(self.calls) - 1]


def timed(id_, start, end, **extra):
    return {"id": id_, "summary": id_.title(), "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


def all_day(id_, start, end):
    return {"id": id_, "summary": id_.title(), "start": {"date": start}, "end": {"date": end}}


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class TestResolveDateRange:
    def test_defaults_to_today(self):
        assert resolve_date_range(None, None, date(2026, 10, 3)) == (date(2026, 10, 3), date(2026, 10, 3))

    def test_end_defaults_to_start(self):
        assert resolve_date_range("2026-10-05", None, date(2026, 10, 3)) == (date(2026, 10, 5), date(2026, 10, 5))

    def test_rejects_bad_format(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            resolve_date_range("10/05/2026", None, date(2026, 10, 3))

    def test_rejects_reversed_range(self):
        with pytest.raises(ValueError, match="on or after"):
            resolve_date_range("2026-10-05", "2026-10-04", date(2026, 10, 3))

    def test_rejects_too_long_range(self):
        with pytest.raises(ValueError, match="at most 31"):
            resolve_date_range("2026-10-01", "2026-11-01", date(2026, 10, 3))


class TestDayBounds:
    def test_single_day_in_dst(self):
        assert day_bounds_utc(date(2026, 10, 3), date(2026, 10, 3), NY) == (utc(2026, 10, 3, 4), utc(2026, 10, 4, 4))

    def test_day_when_clocks_fall_back_is_25_hours(self):
        start, end = day_bounds_utc(date(2026, 11, 1), date(2026, 11, 1), NY)
        assert (start, end) == (utc(2026, 11, 1, 4), utc(2026, 11, 2, 5))

    def test_day_when_clocks_spring_forward_is_23_hours(self):
        start, end = day_bounds_utc(date(2026, 3, 8), date(2026, 3, 8), NY)
        assert (start, end) == (utc(2026, 3, 8, 5), utc(2026, 3, 9, 4))


class TestParseEvent:
    def test_timed_event_converted_to_utc(self):
        event = parse_event(
            timed("dentist", "2026-10-03T15:00:00-04:00", "2026-10-03T16:00:00-04:00", location=" 1 Main St "), NY
        )
        assert event.start == utc(2026, 10, 3, 19)
        assert event.end == utc(2026, 10, 3, 20)
        assert event.all_day is False
        assert event.location == "1 Main St"

    def test_all_day_event_uses_local_midnight(self):
        event = parse_event(all_day("trip", "2026-10-03", "2026-10-04"), NY)
        assert event.all_day is True
        assert event.start == utc(2026, 10, 3, 4)

    def test_cancelled_event_skipped(self):
        raw = timed("x", "2026-10-03T15:00:00Z", "2026-10-03T16:00:00Z", status="cancelled")
        assert parse_event(raw, NY) is None

    def test_attendees_and_organizer(self):
        raw = timed("x", "2026-10-03T15:00:00Z", "2026-10-03T16:00:00Z", organizer={"self": True}, attendees=[
            {"email": "me@x.com", "responseStatus": "accepted", "self": True},
            {"email": "sam@x.com", "optional": True},
            {"email": "room@r.com", "resource": True, "responseStatus": "accepted"},
            {"responseStatus": "accepted"},
        ])
        event = parse_event(raw, NY)
        assert event.is_organizer is True
        assert event.attendees == (
            Attendee("me@x.com", "accepted", is_self=True),
            Attendee("sam@x.com", "needsAction", optional=True),
            Attendee("room@r.com", "accepted", resource=True),
        )

    def test_no_attendees_and_someone_elses_event(self):
        raw = timed("x", "2026-10-03T15:00:00Z", "2026-10-03T16:00:00Z", organizer={"email": "boss@x.com"})
        event = parse_event(raw, NY)
        assert event.attendees == () and event.is_organizer is False

    def test_missing_title_and_blank_location(self):
        raw = timed("x", "2026-10-03T15:00:00Z", "2026-10-03T16:00:00Z", location="  ")
        del raw["summary"]
        event = parse_event(raw, NY)
        assert event.title == "(no title)"
        assert event.location is None


class TestGetEvents:
    def test_follows_pages_and_sorts(self):
        source = FakeSource([
            {"items": [timed("late", "2026-10-03T18:00:00-04:00", "2026-10-03T19:00:00-04:00")], "nextPageToken": "p2"},
            {"items": [
                timed("early", "2026-10-03T09:00:00-04:00", "2026-10-03T10:00:00-04:00"),
                timed("gone", "2026-10-03T11:00:00-04:00", "2026-10-03T12:00:00-04:00", status="cancelled"),
                all_day("holiday", "2026-10-03", "2026-10-04"),
            ]},
        ])

        events = get_events(source, date(2026, 10, 3), date(2026, 10, 3), NY)

        assert [e.id for e in events] == ["holiday", "early", "late"]
        assert [c[2] for c in source.calls] == [None, "p2"]
        assert source.calls[0][:2] == (utc(2026, 10, 3, 4), utc(2026, 10, 4, 4))

    def test_empty_calendar(self):
        assert get_events(FakeSource([{}]), date(2026, 10, 3), date(2026, 10, 3), NY) == []


class TestGetEvent:
    class Source:
        def __init__(self, raw):
            self.raw = raw
            self.asked = []

        def get_event(self, event_id):
            self.asked.append(event_id)
            return self.raw

    def test_returns_parsed_event(self):
        source = self.Source(timed("dentist", "2026-10-03T15:00:00-04:00", "2026-10-03T16:00:00-04:00"))
        assert get_event(source, " dentist ", NY).start == utc(2026, 10, 3, 19)
        assert source.asked == ["dentist"]

    def test_cancelled_event_is_none(self):
        raw = timed("x", "2026-10-03T15:00:00Z", "2026-10-03T16:00:00Z", status="cancelled")
        assert get_event(self.Source(raw), "x", NY) is None

    @pytest.mark.parametrize("bad_id", ["", "   ", 42, "x" * 1025])
    def test_rejects_bad_ids_without_calling_google(self, bad_id):
        source = self.Source({})
        with pytest.raises(ValueError, match="event_id"):
            get_event(source, bad_id, NY)
        assert source.asked == []


class TestGoogleCalendarSourceWrites:
    class Events:
        def __init__(self):
            self.calls = []

        def insert(self, **kwargs):
            self.calls.append(("insert", kwargs))
            return self

        def patch(self, **kwargs):
            self.calls.append(("patch", kwargs))
            return self

        def execute(self):
            return {}

    @pytest.fixture
    def source(self):
        events = self.Events()
        source = GoogleCalendarSource.__new__(GoogleCalendarSource)
        source._service = type("Service", (), {"events": lambda self: events})()
        source._calendar_id = "primary"
        return source, events

    def test_writes_send_no_emails_by_default(self, source):
        source, events = source
        source.insert_event({"summary": "x"})
        source.patch_event("e1", {"summary": "y"})
        assert [kwargs["sendUpdates"] for _, kwargs in events.calls] == ["none", "none"]

    def test_invites_can_send_emails(self, source):
        source, events = source
        source.insert_event({"summary": "x"}, send_updates="all")
        source.patch_event("e1", {"attendees": []}, send_updates="all")
        assert [kwargs["sendUpdates"] for _, kwargs in events.calls] == ["all", "all"]

    def test_single_event_reads_ask_for_attendees_without_names(self, source):
        source, events = source
        source.insert_event({"summary": "x"})
        fields = events.calls[0][1]["fields"]
        assert "attendees(" in fields and "displayName" not in fields


@pytest.mark.parametrize("location, online", [
    ("https://us02web.zoom.us/j/87652641004?pwd=abc", True),
    ("Zoom: zoom.us/j/123", True),
    ("meet.google.com/abc-defg-hij", True),
    ("Microsoft Teams Meeting https://teams.microsoft.com/l/x", True),
    ("3505 S State St, Ann Arbor, MI 48108", False),
    ("Zoo entrance", False),
    (None, False),
])
def test_is_online_location(location, online):
    assert is_online_location(location) is online


class TestFormatEvent:
    def test_timed_event(self):
        event = parse_event(timed("dentist", "2026-10-03T15:00:00-04:00", "2026-10-03T16:30:00-04:00"), NY)
        result = format_event(event, NY)
        assert result["date"] == "Sat, Oct 3"
        assert result["start_time"] == "3:00 PM"
        assert result["end_time"] == "4:30 PM"
        assert result["end_date"] is None
        assert result["start_utc"] == "2026-10-03T19:00:00+00:00"

    def test_overnight_event_shows_end_date(self):
        event = parse_event(timed("party", "2026-10-03T22:00:00-04:00", "2026-10-04T01:00:00-04:00"), NY)
        assert format_event(event, NY)["end_date"] == "Sun, Oct 4"

    def test_single_all_day_event(self):
        result = format_event(parse_event(all_day("holiday", "2026-10-03", "2026-10-04"), NY), NY)
        assert result["all_day"] is True
        assert result["date"] == "Sat, Oct 3"
        assert result["end_date"] is None

    def test_multi_day_all_day_event_end_is_inclusive(self):
        result = format_event(parse_event(all_day("trip", "2026-10-03", "2026-10-06"), NY), NY)
        assert result["end_date"] == "Mon, Oct 5"
