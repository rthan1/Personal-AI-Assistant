"""Background loop that texts users their event reminders."""

import logging
import math
import threading
from datetime import datetime, time, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from assistant.messaging.hermes_send import MessageSender, MessagingError
from assistant.storage.reminders import Reminder, ReminderRepo
from assistant.storage.users import User, UserRepo
from assistant.tools import calendar_service, clock
from assistant.tools.google_auth import GoogleAuthError, credentials_from_token

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30
DEFAULTS_SYNC_INTERVAL = timedelta(minutes=5)
MAX_TITLE_IN_MESSAGE = 120
MAX_LOCATION_IN_MESSAGE = 150
BRIEFING_WINDOW = timedelta(hours=1)  # a missed briefing (app down, Google outage) is dropped after this
MAX_BRIEFING_EVENTS = 8
MAX_TITLE_IN_BRIEFING = 80
MAX_LOCATION_IN_BRIEFING = 60
MAX_NAME_IN_MESSAGE = 40

CalendarSourceFactory = Callable[[Any], calendar_service.EventSource]


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def reminder_message(event: calendar_service.CalendarEvent, now: datetime, tz: ZoneInfo) -> str:
    minutes_left = max(0, math.ceil((event.start - now).total_seconds() / 60))
    if minutes_left >= 120:
        when = f"in about {round(minutes_left / 60)} hours"
    elif minutes_left >= 1:
        when = f"in {minutes_left} min"
    else:
        when = "now"
    start_local = event.start.astimezone(tz)
    day = "" if start_local.date() == now.astimezone(tz).date() else f" {calendar_service.display_date(start_local)}"
    lines = [f"Reminder: {_short(event.title, MAX_TITLE_IN_MESSAGE)} starts at "
             f"{calendar_service.display_time(start_local)}{day} ({when})."]
    if event.location and calendar_service.is_online_location(event.location):
        lines.append("It's an online meeting.")
    elif event.location:
        lines.append(f"Where: {_short(event.location, MAX_LOCATION_IN_MESSAGE)}")
    return "\n".join(lines)


def _greeting(local_now: datetime, name: str | None) -> str:
    part = "morning" if local_now.hour < 12 else "afternoon" if local_now.hour < 17 else "evening"
    first_name = _short(name.split()[0], MAX_NAME_IN_MESSAGE) if name and name.split() else ""
    return f"Good {part}, {first_name}!" if first_name else f"Good {part}!"


def briefing_message(events: list[calendar_service.CalendarEvent], now: datetime, tz: ZoneInfo,
                     name: str | None = None) -> str:
    """Today's events that haven't ended yet, as a short text. `events` are the user's events for today."""
    local_now = now.astimezone(tz)
    greeting = _greeting(local_now, name)
    upcoming = [e for e in events if e.all_day or e.end > now]
    if not upcoming:
        rest = "the rest of today" if events else "today"
        return f"{greeting} Your calendar is clear for {rest} ({calendar_service.display_date(local_now)})."

    count = f"{len(upcoming)} thing{'s' if len(upcoming) != 1 else ''}"
    lines = [f"{greeting} You have {count} on your calendar today ({calendar_service.display_date(local_now)}):"]
    for event in upcoming[:MAX_BRIEFING_EVENTS]:
        when = "All day" if event.all_day else calendar_service.display_time(event.start.astimezone(tz))
        line = f"- {when}: {_short(event.title, MAX_TITLE_IN_BRIEFING)}"
        if event.location and calendar_service.is_online_location(event.location):
            line += " (online)"
        elif event.location:
            line += f" @ {_short(event.location, MAX_LOCATION_IN_BRIEFING)}"
        lines.append(line)
    if len(upcoming) > MAX_BRIEFING_EVENTS:
        lines.append(f"...and {len(upcoming) - MAX_BRIEFING_EVENTS} more.")
    if any(e.location and not e.all_day and not calendar_service.is_online_location(e.location) for e in upcoming):
        lines.append('Text me "when should I leave?" for live travel times.')
    return "\n".join(lines)


class ReminderScheduler:
    def __init__(
        self,
        users: UserRepo,
        reminders: ReminderRepo,
        sender: MessageSender,
        calendar_source_factory: CalendarSourceFactory = calendar_service.GoogleCalendarSource,
        now: Callable[[], datetime] = clock.utc_now,
    ):
        self._users = users
        self._reminders = reminders
        self._sender = sender
        self._calendar_source_factory = calendar_source_factory
        self._now = now
        self._last_defaults_sync: datetime | None = None

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Reminder tick failed")
            stop.wait(CHECK_INTERVAL_SECONDS)

    def tick(self) -> None:
        now = self._now()
        if self._last_defaults_sync is None or now - self._last_defaults_sync >= DEFAULTS_SYNC_INTERVAL:
            self.sync_defaults()
            self._reminders.prune(now)
            self._last_defaults_sync = now
        self.send_due()
        self.send_briefings()

    def sync_defaults(self) -> None:
        """Creates the automatic reminder for each upcoming timed event of users who turned them on."""
        now = self._now()
        for user in self._users.list_with_default_reminders():
            try:
                source = self._source(user)
                tz = ZoneInfo(user.timezone)
                today = now.astimezone(tz).date()
                events = calendar_service.get_events(source, today, today + timedelta(days=1), tz)
            except (GoogleAuthError, HttpError) as exc:
                log.warning("Skipping default reminders for user %s: %s", user.id, exc)
                continue
            for event in events:
                if not event.all_day and event.start > now:
                    self._reminders.ensure_default(user.id, event.id, event.start, user.default_reminder_minutes)

    def send_due(self) -> None:
        for reminder in self._reminders.due(self._now()):
            try:
                self._process(reminder)
            except Exception:
                log.exception("Reminder %s failed; will retry", reminder.id)

    def _process(self, reminder: Reminder) -> None:
        user = self._users.get(reminder.user_id)
        if user is None:
            self._reminders.mark(reminder.id, "skipped")
            return
        if reminder.is_default and user.default_reminder_minutes != reminder.minutes_before:
            self._reminders.mark(reminder.id, "cancelled")
            return
        now = self._now()
        tz = ZoneInfo(user.timezone)
        try:
            event = calendar_service.get_event(self._source(user), reminder.event_id, tz)
        except GoogleAuthError as exc:
            log.warning("Reminder %s skipped, calendar not connected: %s", reminder.id, exc)
            self._reminders.mark(reminder.id, "skipped")
            return
        except HttpError as exc:
            if exc.status_code in (404, 410) or now >= reminder.event_start:
                self._reminders.mark(reminder.id, "skipped")
            return
        if event is None or event.all_day:
            self._reminders.mark(reminder.id, "skipped")
            return
        if event.start != reminder.event_start:
            reminder = self._reminders.reschedule(reminder.id, event.start)
            if reminder.remind_at > now:
                return
        if now >= event.start:
            self._reminders.mark(reminder.id, "skipped")
            return
        try:
            self._sender.send(user.phone, reminder_message(event, now, tz))
        except MessagingError:
            return
        self._reminders.mark(reminder.id, "sent")
        log.info("Sent reminder %s to user %s", reminder.id, user.id)

    def send_briefings(self) -> None:
        for user in self._users.list_with_briefings():
            try:
                self._send_briefing(user)
            except Exception:
                log.exception("Briefing for user %s failed; will retry", user.id)

    def _send_briefing(self, user: User) -> None:
        now = self._now()
        tz = ZoneInfo(user.timezone)
        local_now = now.astimezone(tz)
        today = local_now.date()
        due = datetime.combine(today, time.fromisoformat(user.briefing_time), tz)
        if not due <= local_now < due + BRIEFING_WINDOW or self._users.briefing_sent_on(user.id) == today:
            return
        try:
            events = calendar_service.get_events(self._source(user), today, today, tz)
        except GoogleAuthError as exc:
            log.warning("Briefing skipped for user %s, calendar not connected: %s", user.id, exc)
            self._users.set_briefing_sent_on(user.id, today)
            return
        except HttpError as exc:
            log.warning("Briefing for user %s delayed by a calendar error: %s", user.id, exc)
            return
        try:
            self._sender.send(user.phone, briefing_message(events, now, tz, user.name))
        except MessagingError:
            return
        self._users.set_briefing_sent_on(user.id, today)
        log.info("Sent daily briefing to user %s", user.id)

    def _source(self, user: User) -> calendar_service.EventSource:
        creds, refreshed = credentials_from_token(self._users.get_google_token(user.id))
        if refreshed:
            self._users.set_google_token(user.id, refreshed)
        return self._calendar_source_factory(creds)
