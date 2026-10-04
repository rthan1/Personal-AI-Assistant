from datetime import datetime, timedelta, timezone

import httplib2
import pytest
from googleapiclient.errors import HttpError

from assistant.bridge import service as service_module
from assistant.bridge.service import ToolService
from assistant.tools.maps_service import MapsError

NOW = datetime(2026, 10, 3, 21, 14, tzinfo=timezone.utc)  # 5:14 PM in New York
ANN = "+15551111111"
BOB = "+15552222222"


def event(id_, start, end, location=None, **extra):
    raw = {"id": id_, "summary": id_.title(), "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}
    if location:
        raw["location"] = location
    return raw


def all_day(id_, day, end_day=None):
    """Google all-day end dates are exclusive: a one-day event on the 4th ends on the 5th."""
    end_day = end_day or f"{day[:-2]}{int(day[-2:]) + 1:02d}"
    return {"id": id_, "summary": id_.title(), "start": {"date": day}, "end": {"date": end_day}, "location": "Beach"}


class FakeCalendar:
    def __init__(self, items):
        self.items = {item["id"]: item for item in items}

    def list_events(self, time_min, time_max, page_token):
        return {"items": list(self.items.values())}

    def get_event(self, event_id):
        if event_id not in self.items:
            raise HttpError(httplib2.Response({"status": "404"}), b"not found")
        return self.items[event_id]


class FakeTravel:
    def __init__(self, travel=timedelta(minutes=25), error=None):
        self.travel = travel
        self.error = error
        self.calls = []

    def get_travel_time(self, origin, destination, mode, depart_at):
        self.calls.append((origin, destination, mode))
        if self.error:
            raise self.error
        return self.travel


DENTIST = event("dentist", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00", "1 Main St")


@pytest.fixture
def setup(repo, monkeypatch):
    calendars = {}
    travel = FakeTravel()
    monkeypatch.setattr(service_module, "credentials_from_token", lambda token: (token, None))

    def add_user(phone, items, home="10 Home Rd", **prefs):
        user = repo.upsert(phone, "America/New_York", home_address=home, **prefs)
        repo.set_google_token(user.id, f"token-{phone}")
        calendars[f"token-{phone}"] = FakeCalendar(items)
        return user

    def build(travel_times=travel):
        return ToolService(repo, "https://example.test", calendar_source_factory=lambda creds: calendars[creds],
                           now=lambda: NOW, travel_times=travel_times)

    return add_user, build, travel


def test_plans_next_event_with_a_location(setup):
    add_user, build, travel = setup
    add_user(ANN, [
        event("lunch", "2026-10-03T12:00:00-04:00", "2026-10-03T13:00:00-04:00", "Cafe"),
        event("focus", "2026-10-03T19:00:00-04:00", "2026-10-03T19:30:00-04:00"),
        DENTIST,
    ], buffer_minutes=10)

    result = build().call("plan_departure", ANN, {})

    assert result["event"]["title"] == "Dentist"
    assert result["status"] == "on_time"
    assert result["leave_at"] == "7:25 PM"
    assert result["arrive_at"] == "7:50 PM"
    assert result["travel_minutes"] == 25
    assert result["buffer_minutes"] == 10
    assert travel.calls[0] == ("10 Home Rd", "1 Main St", "drive")


def test_plans_a_specific_event_by_id(setup):
    add_user, build, travel = setup
    later = event("dinner", "2026-10-04T19:00:00-04:00", "2026-10-04T21:00:00-04:00", "5 Food St")
    add_user(ANN, [DENTIST, later], travel_mode="transit")

    result = build().call("plan_departure", ANN, {"event_id": "dinner"})

    assert result["event"]["title"] == "Dinner"
    assert result["leave_date"] == "Sun, Oct 4"
    assert travel.calls[0] == ("10 Home Rd", "5 Food St", "transit")


def test_unknown_event_id_points_to_get_events(setup):
    add_user, build, _ = setup
    add_user(ANN, [DENTIST])
    assert "get_events" in build().call("plan_departure", ANN, {"event_id": "nope"})["error"]


def test_cancelled_event_is_reported(setup):
    add_user, build, _ = setup
    add_user(ANN, [event("gone", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00", "X", status="cancelled")])
    assert "cancelled" in build().call("plan_departure", ANN, {"event_id": "gone"})["error"]


def test_no_home_address_asks_for_it(setup):
    add_user, build, travel = setup
    add_user(ANN, [DENTIST], home=None)
    result = build().call("plan_departure", ANN, {})
    assert "home_address" in result["error"]
    assert travel.calls == []


def test_missing_maps_key_says_not_set_up(setup):
    add_user, build, _ = setup
    add_user(ANN, [DENTIST])
    assert "isn't set up" in build(travel_times=None).call("plan_departure", ANN, {})["error"]


def test_event_without_location_asks_where_it_is(setup):
    add_user, build, travel = setup
    add_user(ANN, [event("interview", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00")])
    result = build().call("plan_departure", ANN, {"event_id": "interview"})
    assert '"Interview" has no location' in result["error"]
    assert travel.calls == []


def test_destination_fills_in_a_missing_location(setup):
    add_user, build, travel = setup
    add_user(ANN, [event("interview", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00")])
    result = build().call("plan_departure", ANN, {"event_id": "interview", "destination": " 9  Office  Park "})
    assert result["destination"] == "9 Office Park"
    assert travel.calls[0][1] == "9 Office Park"


def test_destination_does_not_override_calendar_location(setup):
    add_user, build, travel = setup
    add_user(ANN, [DENTIST])
    build().call("plan_departure", ANN, {"event_id": "dentist", "destination": "Somewhere else"})
    assert travel.calls[0][1] == "1 Main St"


def test_online_meeting_is_not_sent_to_maps(setup):
    add_user, build, travel = setup
    zoom = "https://us02web.zoom.us/j/123?pwd=secret"
    add_user(ANN, [event("standup", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00", zoom)])
    result = build().call("plan_departure", ANN, {"event_id": "standup"})
    assert "online meeting" in result["error"]
    assert travel.calls == []


def test_next_event_skips_online_meetings(setup):
    add_user, build, travel = setup
    add_user(ANN, [
        event("standup", "2026-10-03T18:00:00-04:00", "2026-10-03T18:30:00-04:00", "Google Meet: meet.google.com/abc"),
        DENTIST,
    ])
    assert build().call("plan_departure", ANN, {})["event"]["title"] == "Dentist"


def test_destination_cannot_be_a_link(setup):
    add_user, build, travel = setup
    add_user(ANN, [event("interview", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00")])
    result = build().call("plan_departure", ANN, {"event_id": "interview", "destination": "https://zoom.us/j/1"})
    assert "street address" in result["error"]
    assert travel.calls == []


def test_no_located_events_today_lists_them(setup):
    add_user, build, _ = setup
    add_user(ANN, [event("focus", "2026-10-03T19:00:00-04:00", "2026-10-03T19:30:00-04:00")])
    error = build().call("plan_departure", ANN, {})["error"]
    assert '"Focus"' in error and "destination" in error


def test_no_more_events_today(setup):
    add_user, build, _ = setup
    add_user(ANN, [event("lunch", "2026-10-03T12:00:00-04:00", "2026-10-03T13:00:00-04:00", "Cafe")])
    assert "No more timed events today" in build().call("plan_departure", ANN, {})["error"]


def test_all_day_event_without_time_asks_for_one(setup):
    add_user, build, travel = setup
    add_user(ANN, [all_day("vacation", "2026-10-04")])
    error = build().call("plan_departure", ANN, {"event_id": "vacation"})["error"]
    assert "all-day event on Sun, Oct 4" in error and "what time" in error and "arrive_by" in error
    assert "which day" not in error
    assert travel.calls == []


def test_multi_day_event_asks_which_day_and_lists_the_dates(setup):
    add_user, build, travel = setup
    add_user(ANN, [all_day("hotel", "2026-10-02", end_day="2026-10-06")])
    error = build().call("plan_departure", ANN, {"event_id": "hotel"})["error"]
    assert "runs from Fri, Oct 2 to Mon, Oct 5" in error
    assert "which day and what time" in error
    assert travel.calls == []


def test_multi_day_event_plan_reports_the_day_it_planned_for(setup):
    add_user, build, _ = setup
    add_user(ANN, [all_day("hotel", "2026-10-02", end_day="2026-10-06")], buffer_minutes=10)
    result = build().call("plan_departure", ANN, {"event_id": "hotel", "arrive_by": "2026-10-04T15:00"})
    assert (result["event"]["date"], result["event"]["last_date"]) == ("Fri, Oct 2", "Mon, Oct 5")
    assert (result["target_date"], result["leave_date"]) == ("Sun, Oct 4", "Sun, Oct 4")


def test_all_day_event_with_arrive_by_uses_its_location_and_buffer(setup):
    add_user, build, travel = setup
    add_user(ANN, [all_day("vacation", "2026-10-04")], buffer_minutes=10)
    result = build().call("plan_departure", ANN, {"event_id": "vacation", "arrive_by": "2026-10-04T15:00"})
    assert result["destination"] == "Beach"
    assert (result["target_date"], result["target_time"]) == ("Sun, Oct 4", "3:00 PM")
    assert result["target_is"] == "arrive_by time the user gave"
    assert result["arrive_at"] == "2:50 PM"
    assert result["leave_at"] == "2:25 PM"
    assert result["event"]["all_day"] is True and result["event"]["start_time"] is None


def test_arrive_by_overrides_timed_event_start(setup):
    add_user, build, _ = setup
    add_user(ANN, [DENTIST], buffer_minutes=10)
    result = build().call("plan_departure", ANN, {"event_id": "dentist", "arrive_by": "2026-10-03T19:30"})
    assert result["target_time"] == "7:30 PM"
    assert result["leave_at"] == "6:55 PM"


def test_trip_not_on_calendar_with_destination_and_arrive_by(setup):
    add_user, build, travel = setup
    add_user(ANN, [], buffer_minutes=0)
    result = build().call("plan_departure", ANN, {"destination": "3505 S State St, Ann Arbor, MI",
                                                  "arrive_by": "2026-10-05T12:00"})
    assert result["event"] is None
    assert result["leave_at"] == "11:35 AM" and result["leave_date"] == "Mon, Oct 5"
    assert travel.calls[0] == ("10 Home Rd", "3505 S State St, Ann Arbor, MI", "drive")


def test_arrive_by_with_timezone_offset_is_respected(setup):
    add_user, build, _ = setup
    add_user(ANN, [], buffer_minutes=0)
    result = build().call("plan_departure", ANN, {"destination": "X", "arrive_by": "2026-10-04T19:00:00+00:00"})
    assert result["target_time"] == "3:00 PM"


@pytest.mark.parametrize("arrive_by, message", [
    ("tomorrow at 3", "like 2026-10-04T15:00"),
    ("2026-10-03T17:00", "in the past"),
    ("2026-11-10T12:00", "at most 31 days"),
])
def test_bad_arrive_by_is_rejected(setup, arrive_by, message):
    add_user, build, travel = setup
    add_user(ANN, [])
    result = build().call("plan_departure", ANN, {"destination": "X", "arrive_by": arrive_by})
    assert message in result["error"]
    assert travel.calls == []


def test_event_already_started(setup):
    add_user, build, _ = setup
    add_user(ANN, [event("meeting", "2026-10-03T17:00:00-04:00", "2026-10-03T18:00:00-04:00", "HQ")])
    assert "already started" in build().call("plan_departure", ANN, {"event_id": "meeting"})["error"]


def test_running_late_reports_minutes_late(setup):
    add_user, build, _ = setup
    add_user(ANN, [event("call", "2026-10-03T17:30:00-04:00", "2026-10-03T18:00:00-04:00", "HQ")])
    result = build().call("plan_departure", ANN, {})
    assert result["status"] == "late"
    assert result["leave_at"] == "5:14 PM"
    assert result["late_by_minutes"] == 9


def test_event_after_midnight_shows_leave_date_the_day_before(setup):
    add_user, build, _ = setup
    add_user(ANN, [event("flight", "2026-10-04T00:15:00-04:00", "2026-10-04T03:00:00-04:00", "JFK")],
             buffer_minutes=10)
    build_service = build(travel_times=FakeTravel(timedelta(minutes=30)))
    result = build_service.call("plan_departure", ANN, {"event_id": "flight"})
    assert (result["leave_date"], result["leave_at"]) == ("Sat, Oct 3", "11:35 PM")
    assert result["event"]["date"] == "Sun, Oct 4"


def test_maps_errors_are_passed_on(setup):
    add_user, build, _ = setup
    add_user(ANN, [DENTIST])
    service = build(travel_times=FakeTravel(error=MapsError("Google Maps found no walk route between those places.")))
    assert "no walk route" in service.call("plan_departure", ANN, {})["error"]


def test_each_user_plans_from_their_own_home_and_calendar(setup):
    add_user, build, travel = setup
    add_user(ANN, [DENTIST], home="Ann's House")
    add_user(BOB, [event("gym", "2026-10-03T19:00:00-04:00", "2026-10-03T20:00:00-04:00", "Gym St")],
             home="Bob's Flat", travel_mode="bicycle")
    service = build()

    ann = service.call("plan_departure", ANN, {})
    bob = service.call("plan_departure", BOB, {})
    ann_by_bobs_event = service.call("plan_departure", ANN, {"event_id": "gym"})

    assert ann["event"]["title"] == "Dentist" and bob["event"]["title"] == "Gym"
    assert set(travel.calls) == {("Ann's House", "1 Main St", "drive"), ("Bob's Flat", "Gym St", "bicycle")}
    assert "error" in ann_by_bobs_event
