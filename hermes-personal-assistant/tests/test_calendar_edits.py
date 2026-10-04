from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from assistant.tools.calendar_edits import (
    MAX_GUESTS_PER_CHANGE,
    EventTimes,
    GuestRequest,
    changed_event_times,
    clean_email,
    clean_location,
    clean_title,
    describe,
    find_conflicts,
    free_slots,
    merge_attendees,
    new_event_times,
    parse_guests,
    parse_local_datetime,
    time_fields,
)
from assistant.tools.calendar_service import Attendee, CalendarEvent

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


EARLY_NOW = utc(2026, 10, 1, 12)


def local(hhmm, day=6):
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 10, day, hour, minute, tzinfo=NY).astimezone(timezone.utc)


def busy(id_, start, end, end_day=6):
    return CalendarEvent(id=id_, title=id_.title(), start=local(start), end=local(end, end_day), all_day=False,
                         location=None)


def span(start, end):
    return EventTimes(start_at=local(start), end_at=local(end))


def starts(slots):
    return [s.start_at.astimezone(NY).strftime("%H:%M") for s in slots]


class TestFindConflicts:
    def test_partial_overlap(self):
        assert [e.id for e in find_conflicts(span("14:30", "15:30"), [busy("sync", "15:00", "16:00")])] == ["sync"]

    def test_contained_and_containing(self):
        events = [busy("inside", "15:15", "15:45"), busy("around", "14:00", "17:00")]
        assert {e.id for e in find_conflicts(span("15:00", "16:00"), events)} == {"inside", "around"}

    def test_back_to_back_is_not_a_conflict(self):
        events = [busy("before", "14:00", "15:00"), busy("after", "16:00", "17:00")]
        assert find_conflicts(span("15:00", "16:00"), events) == []

    def test_all_day_events_are_ignored(self):
        trip = all_day_event(local("00:00"), local("00:00", 7))
        assert find_conflicts(span("15:00", "16:00"), [trip]) == []

    def test_new_all_day_event_has_no_conflicts(self):
        times = EventTimes(first_day=date(2026, 10, 6), last_day=date(2026, 10, 6))
        assert find_conflicts(times, [busy("sync", "15:00", "16:00")]) == []

    def test_event_being_moved_ignores_itself(self):
        assert find_conflicts(span("15:30", "16:30"), [busy("self", "15:00", "16:00")], ignore_event_id="self") == []

    def test_event_running_past_midnight(self):
        late = busy("party", "22:00", "01:00", end_day=7)
        times = EventTimes(start_at=local("00:30", 7), end_at=local("01:30", 7))
        assert [e.id for e in find_conflicts(times, [late])] == ["party"]


class TestFreeSlots:
    def test_nearest_slot_before_and_after(self):
        events = [busy("a", "13:00", "14:00"), busy("sync", "15:00", "16:00"), busy("b", "16:30", "18:00")]
        assert starts(free_slots(span("15:00", "16:00"), events, NY, EARLY_NOW)) == ["14:00", "18:00"]

    def test_slot_starts_right_when_an_event_ends(self):
        events = [busy("a", "14:00", "15:00"), busy("sync", "15:00", "15:50")]
        assert starts(free_slots(span("15:00", "15:30"), events, NY, EARLY_NOW)) == ["13:30", "15:50"]

    def test_slots_keep_the_same_length(self):
        slots = free_slots(span("15:00", "16:30"), [busy("sync", "15:00", "16:00")], NY, EARLY_NOW)
        assert all(s.end_at - s.start_at == local("16:30") - local("15:00") for s in slots)
        assert starts(slots) == ["13:30", "16:00"]

    def test_slots_stay_within_the_day(self):
        events = [busy("morning", "07:00", "09:00"), busy("evening", "20:00", "22:00")]
        assert starts(free_slots(span("07:30", "08:30"), events, NY, EARLY_NOW)) == ["09:00"]
        assert starts(free_slots(span("21:00", "22:00"), events, NY, EARLY_NOW)) == ["19:00"]

    def test_no_slots_in_the_past(self):
        now = local("14:10")
        slots = free_slots(span("15:00", "16:00"), [busy("sync", "15:00", "16:00")], NY, now)
        assert starts(slots) == ["16:00"]

    def test_fully_booked_day_has_no_slots(self):
        assert free_slots(span("12:00", "13:00"), [busy("all", "06:00", "23:00")], NY, EARLY_NOW) == []

    def test_moved_event_does_not_block_its_own_slot(self):
        events = [busy("self", "10:00", "11:00"), busy("sync", "15:00", "16:00")]
        slots = free_slots(span("15:00", "16:00"), events, NY, EARLY_NOW, ignore_event_id="self")
        assert starts(slots) == ["14:00", "16:00"]

    def test_all_day_events_do_not_block_slots(self):
        trip = all_day_event(local("00:00"), local("00:00", 7))
        slots = free_slots(span("15:00", "16:00"), [trip, busy("sync", "15:00", "16:00")], NY, EARLY_NOW)
        assert starts(slots) == ["14:00", "16:00"]


class TestCleanEmail:
    def test_lowercases_and_trims(self):
        assert clean_email("  Sam.Lee@Gmail.COM ") == "sam.lee@gmail.com"

    @pytest.mark.parametrize("value", [None, "", "sam", "sam@", "@gmail.com", "sam@gmail", "a b@x.com",
                                       "sam@x.com, bob@y.com", "<sam@x.com>", "x" * 250 + "@x.com"])
    def test_rejects_invalid(self, value):
        with pytest.raises(ValueError, match="valid email"):
            clean_email(value)


class TestParseGuests:
    def test_emails_names_and_name_with_email(self):
        assert parse_guests(["sam@x.com", "Alex", "Jo Smith <JO@y.org>"]) == [
            GuestRequest(None, "sam@x.com"), GuestRequest("Alex", None), GuestRequest("Jo Smith", "jo@y.org"),
        ]

    def test_comma_separated_text(self):
        assert parse_guests("sam@x.com, Alex") == [GuestRequest(None, "sam@x.com"), GuestRequest("Alex", None)]

    def test_blank_items_skipped_and_none_is_empty(self):
        assert parse_guests(["", "  "]) == [] and parse_guests(None) == []

    def test_quoted_name(self):
        assert parse_guests(['"Sam" <sam@x.com>']) == [GuestRequest("Sam", "sam@x.com")]

    def test_bad_email_in_brackets(self):
        with pytest.raises(ValueError, match="valid email"):
            parse_guests(["Sam <not-an-email>"])

    def test_cap(self):
        with pytest.raises(ValueError, match=f"At most {MAX_GUESTS_PER_CHANGE}"):
            parse_guests([f"g{i}@x.com" for i in range(MAX_GUESTS_PER_CHANGE + 1)])

    def test_rejects_other_types(self):
        with pytest.raises(ValueError, match="guests must be a list"):
            parse_guests({"email": "sam@x.com"})


class TestMergeAttendees:
    def test_keeps_existing_rsvps_and_adds_new(self):
        existing = (Attendee("me@x.com", "accepted", is_self=True), Attendee("room@r.com", "accepted", resource=True),
                    Attendee("opt@x.com", "declined", optional=True))
        assert merge_attendees(existing, ["new@x.com"]) == [
            {"email": "me@x.com", "responseStatus": "accepted"},
            {"email": "room@r.com", "responseStatus": "accepted", "resource": True},
            {"email": "opt@x.com", "responseStatus": "declined", "optional": True},
            {"email": "new@x.com"},
        ]

    def test_skips_people_already_invited_case_insensitively(self):
        assert merge_attendees((Attendee("Sam@X.com"),), ["sam@x.com", "a@x.com", "a@x.com"]) == [
            {"email": "Sam@X.com", "responseStatus": "needsAction"}, {"email": "a@x.com"},
        ]
