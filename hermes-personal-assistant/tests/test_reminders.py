import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httplib2
import pytest
from googleapiclient.errors import HttpError

from assistant.bridge import service as service_module
from assistant.bridge.service import ToolService
from assistant.messaging.hermes_send import HermesSender, MessagingError, safe_text
from assistant.scheduler import reminders as scheduler_module
from assistant.scheduler.reminders import ReminderScheduler
from assistant.storage.reminders import ReminderRepo
from assistant.tools import preferences
from test_plan_departure import FakeCalendar, all_day, event

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 3, 21, 0, tzinfo=timezone.utc)  # 5:00 PM in New York
ANN = "+15551111111"
BOB = "+15552222222"
DENTIST = event("dentist", "2026-10-03T19:00:00-04:00", "2026-10-03T20:00:00-04:00", "1 Main St")
DENTIST_START = datetime(2026, 10, 3, 23, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class FakeSender:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, phone, text):
        if self.fail:
            raise MessagingError("down")
        self.sent.append((phone, text))


@pytest.fixture
def world(repo, conn, monkeypatch):
    calendars = {}
    clock = Clock()
    sender = FakeSender()
    reminders = ReminderRepo(conn)
    for module in (service_module, scheduler_module):
        monkeypatch.setattr(module, "credentials_from_token", lambda token: (token, None))

    def add_user(phone, items=(), **prefs):
        user = repo.upsert(phone, "America/New_York", **prefs)
        repo.set_google_token(user.id, f"token-{phone}")
        calendars[f"token-{phone}"] = FakeCalendar([dict(item) for item in items])
        return user

    def factory(creds):
        return calendars[creds]

    service = ToolService(repo, "https://example.test", calendar_source_factory=factory, now=clock,
                          reminders=reminders)
    scheduler = ReminderScheduler(repo, reminders, sender, calendar_source_factory=factory, now=clock)

    class World:
        pass

    w = World()
    w.add_user, w.service, w.scheduler, w.sender, w.clock = add_user, service, scheduler, sender, clock
    w.calendars, w.reminders, w.repo = calendars, reminders, repo
    return w


# --- set_reminder / list_reminders / cancel_reminder tools ---

def test_set_reminder_reports_when_it_will_fire(world):
    world.add_user(ANN, [DENTIST])
    result = world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    assert result["ok"] is True
    assert result["event_title"] == "Dentist"
    assert result["remind_at"] == "Sat, Oct 3 6:30 PM"
    assert result["automatic"] is False


def test_set_reminder_accepts_whole_float_minutes(world):
    world.add_user(ANN, [DENTIST])
    assert world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30.0})["ok"] is True


@pytest.mark.parametrize("minutes, message", [(0, "between 1 and 1440"), (2000, "between 1 and 1440"),
                                              ("soon", "whole number")])
def test_set_reminder_rejects_bad_minutes(world, minutes, message):
    world.add_user(ANN, [DENTIST])
    assert message in world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": minutes})["error"]


def test_set_reminder_refuses_all_day_events(world):
    world.add_user(ANN, [all_day("trip", "2026-10-04")])
    assert "all-day" in world.service.call("set_reminder", ANN, {"event_id": "trip", "minutes_before": 30})["error"]


def test_set_reminder_refuses_when_event_is_too_soon(world):
    world.add_user(ANN, [DENTIST])
    error = world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 180})["error"]
    assert "starts in 120 min" in error


def test_set_reminder_refuses_started_events(world):
    world.add_user(ANN, [event("call", "2026-10-03T16:30:00-04:00", "2026-10-03T17:30:00-04:00")])
    assert "already started" in world.service.call("set_reminder", ANN, {"event_id": "call", "minutes_before": 5})["error"]


def test_set_reminder_unknown_event_points_to_get_events(world):
    world.add_user(ANN)
    assert "get_events" in world.service.call("set_reminder", ANN, {"event_id": "nope", "minutes_before": 5})["error"]


def test_list_reminders_includes_titles(world):
    world.add_user(ANN, [DENTIST])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    listed = world.service.call("list_reminders", ANN, {})
    assert [(r["event_title"], r["minutes_before"]) for r in listed["reminders"]] == [("Dentist", 30)]
    assert listed["default_minutes_before_every_event"] is None


def test_cancel_reminder_by_id_and_all(world):
    world.add_user(ANN, [DENTIST])
    first = world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})["reminder_id"]
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 10})
    assert world.service.call("cancel_reminder", ANN, {"reminder_id": first})["cancelled"] == 1
    assert world.service.call("cancel_reminder", ANN, {"reminder_id": "all"})["cancelled"] == 1
    assert world.service.call("list_reminders", ANN, {})["reminders"] == []


def test_cancel_unknown_reminder_is_an_error(world):
    world.add_user(ANN)
    assert "list_reminders" in world.service.call("cancel_reminder", ANN, {"reminder_id": 99})["error"]


def test_users_cannot_see_or_cancel_each_others_reminders(world):
    world.add_user(ANN, [DENTIST])
    world.add_user(BOB)
    ann_id = world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})["reminder_id"]
    assert world.service.call("list_reminders", BOB, {})["reminders"] == []
    assert "error" in world.service.call("cancel_reminder", BOB, {"reminder_id": ann_id})
    assert world.service.call("cancel_reminder", BOB, {"reminder_id": "all"})["cancelled"] == 0
    assert len(world.service.call("list_reminders", ANN, {})["reminders"]) == 1


def test_cannot_set_reminder_on_another_users_event(world):
    world.add_user(ANN)
    world.add_user(BOB, [DENTIST])
    assert "error" in world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})


def test_not_configured_says_not_set_up(world, repo):
    world.add_user(ANN, [DENTIST])
    service = ToolService(repo, "https://example.test", calendar_source_factory=lambda c: world.calendars[c],
                          now=world.clock)
    assert "aren't set up" in service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 5})["error"]


# --- default reminder preference ---

@pytest.mark.parametrize("value, expected", [(15, 15), ("30", 30), (0, None), ("off", None)])
def test_default_reminder_preference(world, value, expected):
    world.add_user(ANN)
    result = world.service.call("set_preference", ANN, {"key": "default_reminder_minutes", "value": value})
    assert result["value"] == expected


def test_context_mentions_default_reminder(world):
    world.add_user(ANN, default_reminder_minutes=15)
    assert "reminder before every event: 15 min" in world.service.context(ANN)["context"]


# --- scheduler ---

def test_sends_reminder_when_due(world):
    world.add_user(ANN, [DENTIST])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    world.scheduler.send_due()
    assert world.sender.sent == []

    world.clock.now = DENTIST_START - timedelta(minutes=30)
    world.scheduler.send_due()
    assert world.sender.sent == [(ANN, "Reminder: Dentist starts at 7:00 PM (in 30 min).\nWhere: 1 Main St")]

    world.scheduler.send_due()
    assert len(world.sender.sent) == 1


def test_moved_event_reschedules_reminder(world):
    world.add_user(ANN, [DENTIST])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    world.calendars[f"token-{ANN}"].items["dentist"] = event(
        "dentist", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00", "1 Main St")

    world.clock.now = DENTIST_START - timedelta(minutes=30)
    world.scheduler.send_due()
    assert world.sender.sent == []

    world.clock.now = DENTIST_START + timedelta(minutes=30)
    world.scheduler.send_due()
    assert "starts at 8:00 PM (in 30 min)" in world.sender.sent[0][1]


def test_cancelled_event_is_skipped(world):
    world.add_user(ANN, [DENTIST])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    world.calendars[f"token-{ANN}"].items["dentist"]["status"] = "cancelled"
    world.clock.now = DENTIST_START - timedelta(minutes=30)
    world.scheduler.send_due()
    assert world.sender.sent == []
    assert world.reminders.list_pending(world.repo.get_by_phone(ANN).id) == []


def test_deleted_event_is_skipped(world):
    world.add_user(ANN, [DENTIST])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    del world.calendars[f"token-{ANN}"].items["dentist"]
    world.clock.now = DENTIST_START - timedelta(minutes=30)
    world.scheduler.send_due()
    assert world.sender.sent == []
    assert world.reminders.list_pending(world.repo.get_by_phone(ANN).id) == []


def test_reminder_missed_until_event_start_is_skipped(world):
    world.add_user(ANN, [DENTIST])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    world.clock.now = DENTIST_START + timedelta(minutes=1)
    world.scheduler.send_due()
    assert world.sender.sent == []


def test_late_reminder_still_sent_before_event_starts(world):
    world.add_user(ANN, [DENTIST])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    world.clock.now = DENTIST_START - timedelta(minutes=5)
    world.scheduler.send_due()
    assert "(in 5 min)" in world.sender.sent[0][1]


def test_failed_send_is_retried(world):
    world.add_user(ANN, [DENTIST])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    world.clock.now = DENTIST_START - timedelta(minutes=30)
    world.sender.fail = True
    world.scheduler.send_due()
    world.sender.fail = False
    world.scheduler.send_due()
    assert len(world.sender.sent) == 1


def test_calendar_outage_is_retried(world):
    world.add_user(ANN, [DENTIST])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    calendar = world.calendars[f"token-{ANN}"]
    real_get = calendar.get_event
    calendar.get_event = lambda event_id: (_ for _ in ()).throw(HttpError(httplib2.Response({"status": "503"}), b""))
    world.clock.now = DENTIST_START - timedelta(minutes=30)
    world.scheduler.send_due()
    calendar.get_event = real_get
    world.scheduler.send_due()
    assert len(world.sender.sent) == 1


def test_online_meeting_link_is_not_sent(world):
    zoom = event("standup", "2026-10-03T19:00:00-04:00", "2026-10-03T19:30:00-04:00", "https://zoom.us/j/1?pwd=x")
    world.add_user(ANN, [zoom])
    world.service.call("set_reminder", ANN, {"event_id": "standup", "minutes_before": 10})
    world.clock.now = DENTIST_START - timedelta(minutes=10)
    world.scheduler.send_due()
    text = world.sender.sent[0][1]
    assert "online meeting" in text and "zoom.us" not in text


def test_each_reminder_goes_to_its_owner(world):
    world.add_user(ANN, [DENTIST])
    world.add_user(BOB, [event("gym", "2026-10-03T19:00:00-04:00", "2026-10-03T20:00:00-04:00", "Gym St")])
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    world.service.call("set_reminder", BOB, {"event_id": "gym", "minutes_before": 30})
    world.clock.now = DENTIST_START - timedelta(minutes=30)
    world.scheduler.send_due()
    assert sorted((phone, text.split(" starts")[0]) for phone, text in world.sender.sent) == [
        (ANN, "Reminder: Dentist"), (BOB, "Reminder: Gym")]


def test_default_reminders_cover_upcoming_timed_events(world):
    world.add_user(ANN, [DENTIST, all_day("trip", "2026-10-04"),
                         event("lunch", "2026-10-03T12:00:00-04:00", "2026-10-03T13:00:00-04:00")],
                   default_reminder_minutes=15)
    world.add_user(BOB, [event("gym", "2026-10-03T19:00:00-04:00", "2026-10-03T20:00:00-04:00")])
    world.scheduler.sync_defaults()
    ann = world.repo.get_by_phone(ANN)
    assert [(r.event_id, r.minutes_before, r.is_default) for r in world.reminders.list_pending(ann.id)] == [
        ("dentist", 15, True)]
    assert world.reminders.list_pending(world.repo.get_by_phone(BOB).id) == []


def test_cancelled_default_reminder_is_not_recreated(world):
    world.add_user(ANN, [DENTIST], default_reminder_minutes=15)
    world.scheduler.sync_defaults()
    world.service.call("cancel_reminder", ANN, {"reminder_id": "all"})
    world.scheduler.sync_defaults()
    assert world.service.call("list_reminders", ANN, {})["reminders"] == []


def test_changing_default_minutes_replaces_automatic_reminders(world):
    world.add_user(ANN, [DENTIST], default_reminder_minutes=15)
    world.scheduler.sync_defaults()
    world.service.call("set_preference", ANN, {"key": "default_reminder_minutes", "value": 45})
    world.scheduler.sync_defaults()
    listed = world.service.call("list_reminders", ANN, {})["reminders"]
    assert [(r["minutes_before"], r["automatic"]) for r in listed] == [(45, True)]


def test_turning_defaults_off_stops_automatic_reminders(world):
    world.add_user(ANN, [DENTIST], default_reminder_minutes=15)
    world.service.call("set_reminder", ANN, {"event_id": "dentist", "minutes_before": 30})
    world.scheduler.sync_defaults()
    world.service.call("set_preference", ANN, {"key": "default_reminder_minutes", "value": "off"})
    listed = world.service.call("list_reminders", ANN, {})["reminders"]
    assert [(r["minutes_before"], r["automatic"]) for r in listed] == [(30, False)]


def test_tick_syncs_defaults_and_sends(world):
    world.add_user(ANN, [DENTIST], default_reminder_minutes=15)
    world.clock.now = DENTIST_START - timedelta(minutes=15)
    world.scheduler.tick()
    assert world.sender.sent[0][1].startswith("Reminder: Dentist starts at 7:00 PM (in 15 min).")


def test_media_directives_in_titles_are_defanged(world):
    world.add_user(ANN, [event("x", "2026-10-03T19:00:00-04:00", "2026-10-03T20:00:00-04:00")])
    world.calendars[f"token-{ANN}"].items["x"]["summary"] = "MEDIA:C:\\secret.env"
    listed_events = world.service.call("get_events", ANN, {})
    assert "MEDIA:" not in json.dumps(listed_events)


# --- daily briefing ---

LUNCH = event("lunch", "2026-10-03T12:00:00-04:00", "2026-10-03T13:00:00-04:00", "Cafe")
STANDUP = event("standup", "2026-10-03T18:00:00-04:00", "2026-10-03T18:15:00-04:00", "https://meet.google.com/abc")


@pytest.mark.parametrize("value, expected", [
    ("7:30 AM", "07:30"), ("7am", "07:00"), ("7 p.m.", "19:00"), ("12am", "00:00"), ("12 PM", "12:00"),
    ("19:05", "19:05"), (7, "07:00"), (7.0, "07:00"), ("0:00", "00:00"),
    ("off", None), (0, None), (None, None),
])
def test_parse_clock_time(value, expected):
    assert preferences.parse_clock_time(value) == expected


@pytest.mark.parametrize("value", ["25:00", "7:60", "13pm", "0am", "noonish", "7:5", True])
def test_parse_clock_time_rejects_bad_times(value):
    with pytest.raises(ValueError):
        preferences.parse_clock_time(value)


def test_briefing_preference_is_saved_and_shown_in_context(world):
    world.add_user(ANN)
    result = world.service.call("set_preference", ANN, {"key": "briefing_time", "value": "7:30 am"})
    assert result["value"] == "07:30" and result["sends_daily_at"] == "7:30 AM"
    assert world.service.call("get_preferences", ANN, {})["briefing_time"] == "07:30"
    assert "daily briefing text: 7:30 AM" in world.service.context(ANN)["context"]


def test_briefing_off_by_default_and_can_be_turned_off(world):
    world.add_user(ANN, briefing_time="07:30")
    assert world.service.call("set_preference", ANN, {"key": "briefing_time", "value": "off"})["value"] is None
    assert "daily briefing text: off" in world.service.context(ANN)["context"]


def test_briefing_message_lists_remaining_events():
    events = [scheduler_module.calendar_service.parse_event(raw, NY)
              for raw in (all_day("trip", "2026-10-03"), LUNCH, STANDUP, DENTIST)]
    text = scheduler_module.briefing_message(events, NOW, NY, "Ann Lee")
    assert text == (
        "Good evening, Ann! You have 3 things on your calendar today (Sat, Oct 3):\n"
        "- All day: Trip @ Beach\n"
        "- 6:00 PM: Standup (online)\n"
        "- 7:00 PM: Dentist @ 1 Main St\n"
        'Text me "when should I leave?" for live travel times.'
    )


def test_briefing_message_for_empty_and_finished_days():
    lunch = scheduler_module.calendar_service.parse_event(LUNCH, NY)
    morning = datetime(2026, 10, 3, 11, 0, tzinfo=timezone.utc)
    assert scheduler_module.briefing_message([], morning, NY) == (
        "Good morning! Your calendar is clear for today (Sat, Oct 3).")
    assert "clear for the rest of today" in scheduler_module.briefing_message([lunch], NOW, NY)


def test_briefing_message_caps_long_days():
    events = [scheduler_module.calendar_service.parse_event(
        event(f"e{i}", "2026-10-03T19:00:00-04:00", "2026-10-03T20:00:00-04:00"), NY) for i in range(10)]
    text = scheduler_module.briefing_message(events, NOW, NY)
    assert text.count("\n- ") == scheduler_module.MAX_BRIEFING_EVENTS and text.endswith("...and 2 more.")


def test_briefing_sent_once_at_its_time(world):
    world.add_user(ANN, [DENTIST], briefing_time="17:30", name="Ann")
    world.scheduler.send_briefings()
    assert world.sender.sent == []

    world.clock.now = NOW + timedelta(minutes=30)
    world.scheduler.send_briefings()
    world.scheduler.send_briefings()
    assert len(world.sender.sent) == 1
    phone, text = world.sender.sent[0]
    assert phone == ANN and text.startswith("Good evening, Ann! You have 1 thing") and "Dentist" in text


def test_briefing_sent_again_the_next_day(world):
    world.add_user(ANN, briefing_time="17:00")
    world.scheduler.send_briefings()
    world.clock.now = NOW + timedelta(days=1)
    world.scheduler.send_briefings()
    assert len(world.sender.sent) == 2


def test_missed_briefing_is_dropped_after_the_window(world):
    world.add_user(ANN, briefing_time="15:59")
    world.scheduler.send_briefings()
    assert world.sender.sent == []


def test_resetting_briefing_time_allows_another_today(world):
    world.add_user(ANN, briefing_time="17:00")
    world.scheduler.send_briefings()
    world.service.call("set_preference", ANN, {"key": "briefing_time", "value": "4:30 PM"})
    world.scheduler.send_briefings()
    assert len(world.sender.sent) == 2


def test_each_briefing_reads_its_owners_calendar(world):
    world.add_user(ANN, [DENTIST], briefing_time="17:00")
    world.add_user(BOB, [event("gym", "2026-10-03T19:00:00-04:00", "2026-10-03T20:00:00-04:00")],
                   briefing_time="17:00")
    world.add_user("+15553333333", [DENTIST])
    world.scheduler.send_briefings()
    sent = dict(world.sender.sent)
    assert set(sent) == {ANN, BOB}
    assert "Dentist" in sent[ANN] and "Gym" not in sent[ANN]
    assert "Gym" in sent[BOB] and "Dentist" not in sent[BOB]


def test_failed_briefing_send_is_retried(world):
    world.add_user(ANN, briefing_time="17:00")
    world.sender.fail = True
    world.scheduler.send_briefings()
    world.sender.fail = False
    world.scheduler.send_briefings()
    assert len(world.sender.sent) == 1


def test_briefing_calendar_outage_is_retried(world):
    world.add_user(ANN, [DENTIST], briefing_time="17:00")
    calendar = world.calendars[f"token-{ANN}"]
    real_list = calendar.list_events
    calendar.list_events = lambda *a: (_ for _ in ()).throw(HttpError(httplib2.Response({"status": "503"}), b""))
    world.scheduler.send_briefings()
    calendar.list_events = real_list
    world.scheduler.send_briefings()
    assert len(world.sender.sent) == 1


def test_briefing_skipped_for_the_day_when_calendar_disconnected(world, monkeypatch):
    world.add_user(ANN, briefing_time="17:00")

    def disconnected(token):
        raise scheduler_module.GoogleAuthError("expired")

    monkeypatch.setattr(scheduler_module, "credentials_from_token", disconnected)
    world.scheduler.send_briefings()
    monkeypatch.setattr(scheduler_module, "credentials_from_token", lambda token: (token, None))
    world.scheduler.send_briefings()
    assert world.sender.sent == []


def test_tick_sends_briefings(world):
    world.add_user(ANN, briefing_time="17:00")
    world.scheduler.tick()
    assert len(world.sender.sent) == 1


# --- hermes send wrapper ---

class FakeRun:
    def __init__(self, returncode=0, stdout='{"success": true}', error=None):
        self.returncode, self.stdout, self.error = returncode, stdout, error
        self.calls, self.bodies = [], []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        path = cmd[cmd.index("--file") + 1]
        with open(path, encoding="utf-8") as handle:
            self.bodies.append(handle.read())
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "")


def test_sender_passes_body_through_a_file_not_the_command_line(tmp_path):
    run = FakeRun()
    HermesSender(tmp_path / "hermes.cmd", runner=run).send(ANN, "Lunch & Learn | 5 ünicode")
    cmd, kwargs = run.calls[0]
    assert cmd[1:4] == ["send", "--to", f"photon:any;-;{ANN}"]
    assert "Lunch" not in " ".join(cmd)
    assert run.bodies == ["Lunch & Learn | 5 ünicode"]
    assert kwargs["timeout"] > 0


def test_sender_strips_media_directives(tmp_path):
    run = FakeRun()
    HermesSender(tmp_path / "hermes.cmd", runner=run).send(ANN, "Reminder: MEDIA:C:\\x.env and media : y")
    assert "MEDIA:" not in run.bodies[0] and "media :" not in run.bodies[0]


def test_safe_text_breaks_document_markers():
    assert "[[" not in safe_text("[[as_document]]")


def test_sender_refuses_malformed_phone(tmp_path):
    run = FakeRun()
    with pytest.raises(MessagingError):
        HermesSender(tmp_path / "hermes.cmd", runner=run).send("+1 & calc", "hi")
    assert run.calls == []


@pytest.mark.parametrize("run", [
    FakeRun(returncode=1, stdout='{"error": "target not allowed"}'),
    FakeRun(stdout='{"success": false}'),
    FakeRun(stdout="not json"),
    FakeRun(error=subprocess.TimeoutExpired("hermes", 90)),
])
def test_sender_failures_raise(tmp_path, run):
    with pytest.raises(MessagingError):
        HermesSender(tmp_path / "hermes.cmd", runner=run).send(ANN, "hi")


def test_sender_removes_temp_file(tmp_path):
    run = FakeRun()
    HermesSender(tmp_path / "hermes.cmd", runner=run).send(ANN, "hi")
    path = run.calls[0][0][run.calls[0][0].index("--file") + 1]
    assert not os.path.exists(path)
