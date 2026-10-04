"""Validation, Google request bodies, and summaries for creating and changing events. Pure: no I/O."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from assistant.tools import preferences
from assistant.tools.calendar_service import Attendee, CalendarEvent, display_date, display_time

MAX_TITLE_LENGTH = 200
MAX_GUESTS_PER_CHANGE = 10
MAX_EMAIL_LENGTH = 254
MAX_GUEST_NAME_LENGTH = 60
EMAIL = re.compile(r"[^@\s<>()\[\],;:\"]+@[^@\s<>()\[\],;:\"]+\.[a-z]{2,}")
NAME_WITH_EMAIL = re.compile(r"(.*?)\s*<([^<>]*)>")
DEFAULT_DURATION_MINUTES = 60
MAX_TIMED_DURATION = timedelta(days=14)
MAX_ALL_DAY_DAYS = 31
FREE_SLOT_DAY_START = time(7, 0)
FREE_SLOT_DAY_END = time(22, 0)
FREE_SLOT_STEP = timedelta(minutes=15)


@dataclass(frozen=True)
class EventTimes:
    """Either a timed span (UTC instants, end exclusive) or an all-day span (local dates, last day inclusive)."""

    start_at: datetime | None = None
    end_at: datetime | None = None
    first_day: date | None = None
    last_day: date | None = None

    @property
    def all_day(self) -> bool:
        return self.first_day is not None


@dataclass(frozen=True)
class GuestRequest:
    """One guest as the model passed it: an email, a contact name to look up, or both."""

    name: str | None
    email: str | None


def clean_title(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split())
    if not text:
        raise ValueError("title must not be empty.")
    if len(text) > MAX_TITLE_LENGTH:
        raise ValueError(f"title must be at most {MAX_TITLE_LENGTH} characters.")
    return text


def clean_location(value: Any) -> str | None:
    text = " ".join(str(value if value is not None else "").split())
    if not text:
        return None
    if len(text) > preferences.MAX_ADDRESS_LENGTH:
        raise ValueError(f"location must be at most {preferences.MAX_ADDRESS_LENGTH} characters.")
    return text


def clean_email(value: Any) -> str:
    text = str(value if value is not None else "").strip().lower()
    if len(text) > MAX_EMAIL_LENGTH or not EMAIL.fullmatch(text):
        raise ValueError(f"{json.dumps(text[:MAX_EMAIL_LENGTH])} isn't a valid email address. Ask the user for it.")
    return text


def clean_guest_name(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split()).strip('"')
    if not text:
        raise ValueError("A guest's name must not be empty.")
    if any(char in text for char in "@<>,;"):
        raise ValueError("A guest's name can't contain @ < > , or ;.")
    if len(text) > MAX_GUEST_NAME_LENGTH:
        raise ValueError(f"A guest's name must be at most {MAX_GUEST_NAME_LENGTH} characters.")
    return text


def parse_guests(value: Any) -> list[GuestRequest]:
    """Guests from tool args: a list (or comma-separated text) of emails, names, or "Name <email>"."""
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[,;\n]", value)
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("guests must be a list of emails or saved contact names.")
    guests = []
    for item in items:
        text = " ".join(str(item if item is not None else "").split())
        if not text:
            continue
        match = NAME_WITH_EMAIL.fullmatch(text)
        if match:
            name = match.group(1).strip().strip('"')
            guests.append(GuestRequest(clean_guest_name(name) if name else None, clean_email(match.group(2))))
        elif "@" in text:
            guests.append(GuestRequest(None, clean_email(text)))
        else:
            guests.append(GuestRequest(clean_guest_name(text), None))
    if len(guests) > MAX_GUESTS_PER_CHANGE:
        raise ValueError(f"At most {MAX_GUESTS_PER_CHANGE} guests can be invited at once.")
    return guests


def merge_attendees(existing: tuple[Attendee, ...], new_emails: list[str]) -> list[dict[str, Any]]:
    """The full attendee list for a patch: Google replaces the whole list, so existing guests and their RSVPs stay."""
    body: list[dict[str, Any]] = []
    for attendee in existing:
        entry: dict[str, Any] = {"email": attendee.email, "responseStatus": attendee.response_status}
        if attendee.optional:
            entry["optional"] = True
        if attendee.resource:
            entry["resource"] = True
        body.append(entry)
    known = {a.email.lower() for a in existing}
    for email in new_emails:
        if email.lower() not in known:
            body.append({"email": email})
            known.add(email.lower())
    return body


def parse_local_datetime(value: Any, tz: ZoneInfo, field: str) -> datetime:
    """A local YYYY-MM-DDTHH:MM in the user's timezone, as a UTC instant."""
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be a local date and time like 2026-10-04T15:00.") from exc
    if len(str(value).strip()) <= 10:
        raise ValueError(f"{field} needs a time too, like 2026-10-04T15:00 (or set all_day for an all-day event).")
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)).astimezone(timezone.utc)


def parse_day(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be a date like 2026-10-04 for all-day events.") from exc


def is_flag_set(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"true", "yes", "1"}


def _duration(value: Any) -> timedelta:
    minutes = preferences.parse_whole_number(value, "duration_minutes must be a whole number of minutes.")
    if minutes < 1:
        raise ValueError("duration_minutes must be at least 1.")
    return timedelta(minutes=minutes)


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _validated(times: EventTimes) -> EventTimes:
    if times.all_day:
        if times.last_day < times.first_day:
            raise ValueError("The end date must be on or after the start date.")
        if (times.last_day - times.first_day).days + 1 > MAX_ALL_DAY_DAYS:
            raise ValueError(f"All-day events can span at most {MAX_ALL_DAY_DAYS} days.")
        return times
    if times.end_at <= times.start_at:
        raise ValueError("The end must be after the start.")
    if times.end_at - times.start_at > MAX_TIMED_DURATION:
        raise ValueError("Timed events can last at most 14 days. Use an all-day event for longer spans.")
    return times


def new_event_times(args: dict, tz: ZoneInfo) -> EventTimes:
    """Times for a new event from tool args: start plus end or duration_minutes (default 60), or all_day dates."""
    start, end = args.get("start"), args.get("end")
    if not _present(start):
        raise ValueError("start is required.")
    if is_flag_set(args.get("all_day")):
        first = parse_day(start, "start")
        last = parse_day(end, "end") if _present(end) else first
        return _validated(EventTimes(first_day=first, last_day=last))
    start_at = parse_local_datetime(start, tz, "start")
    if _present(end):
        end_at = parse_local_datetime(end, tz, "end")
    elif _present(args.get("duration_minutes")):
        end_at = start_at + _duration(args["duration_minutes"])
    else:
        end_at = start_at + timedelta(minutes=DEFAULT_DURATION_MINUTES)
    return _validated(EventTimes(start_at=start_at, end_at=end_at))


def event_times(event: CalendarEvent, tz: ZoneInfo) -> EventTimes:
    if event.all_day:
        return EventTimes(
            first_day=event.start.astimezone(tz).date(),
            last_day=(event.end.astimezone(tz) - timedelta(days=1)).date(),
        )
    return EventTimes(start_at=event.start, end_at=event.end)


def changed_event_times(event: CalendarEvent, args: dict, tz: ZoneInfo) -> EventTimes | None:
    """New times for an existing event, or None if args don't move it. Moving only the start keeps the length."""
    start, end = args.get("start"), args.get("end")
    if not _present(start) and not _present(end):
        return None
    current = event_times(event, tz)
    if event.all_day:
        first = parse_day(start, "start") if _present(start) else current.first_day
        if _present(end):
            last = parse_day(end, "end")
        else:
            last = first + (current.last_day - current.first_day)
        return _validated(EventTimes(first_day=first, last_day=last))
    start_at = parse_local_datetime(start, tz, "start") if _present(start) else current.start_at
    if _present(end):
        end_at = parse_local_datetime(end, tz, "end")
    else:
        end_at = start_at + (current.end_at - current.start_at)
    return _validated(EventTimes(start_at=start_at, end_at=end_at))


def time_fields(times: EventTimes, tz: ZoneInfo) -> dict[str, dict[str, str]]:
    """Google's start/end fields. All-day end dates are exclusive in Google."""
    if times.all_day:
        return {
            "start": {"date": times.first_day.isoformat()},
            "end": {"date": (times.last_day + timedelta(days=1)).isoformat()},
        }
    return {
        "start": {"dateTime": times.start_at.astimezone(tz).isoformat(), "timeZone": tz.key},
        "end": {"dateTime": times.end_at.astimezone(tz).isoformat(), "timeZone": tz.key},
    }


def describe(title: str, times: EventTimes, location: str | None, tz: ZoneInfo) -> dict[str, Any]:
    """How the event reads to the user, in their timezone."""
    if times.all_day:
        return {
            "title": title,
            "all_day": True,
            "date": display_date(datetime.combine(times.first_day, datetime.min.time())),
            "end_date": (display_date(datetime.combine(times.last_day, datetime.min.time()))
                         if times.last_day != times.first_day else None),
            "location": location,
        }
    start_local, end_local = times.start_at.astimezone(tz), times.end_at.astimezone(tz)
    return {
        "title": title,
        "all_day": False,
        "date": display_date(start_local),
        "start_time": display_time(start_local),
        "end_time": display_time(end_local),
        "end_date": display_date(end_local) if end_local.date() != start_local.date() else None,
        "location": location,
    }


def describe_event(event: CalendarEvent, tz: ZoneInfo) -> dict[str, Any]:
    return describe(event.title, event_times(event, tz), event.location, tz)


def _busy(events: list[CalendarEvent], ignore_event_id: str | None) -> list[CalendarEvent]:
    """Events that block time. All-day events (trips, birthdays) would clash with everything, so they don't."""
    return [e for e in events if not e.all_day and e.id != ignore_event_id]


def find_conflicts(times: EventTimes, events: list[CalendarEvent],
                   ignore_event_id: str | None = None) -> list[CalendarEvent]:
    """Timed events that overlap a timed span. Back-to-back events (one ends as the other starts) don't."""
    if times.all_day:
        return []
    return [e for e in _busy(events, ignore_event_id) if e.start < times.end_at and times.start_at < e.end]


def free_slots(times: EventTimes, events: list[CalendarEvent], tz: ZoneInfo, now: datetime,
               ignore_event_id: str | None = None) -> list[EventTimes]:
    """The closest free slots of the same length on the same local day: at most one before and one after.

    Slots stay within FREE_SLOT_DAY_START..FREE_SLOT_DAY_END local time, start in the future, and start on a
    quarter hour or right when a busy event ends.
    """
    if times.all_day:
        return []
    length = times.end_at - times.start_at
    day = times.start_at.astimezone(tz).date()
    window_start = datetime.combine(day, FREE_SLOT_DAY_START, tz).astimezone(timezone.utc)
    window_end = datetime.combine(day, FREE_SLOT_DAY_END, tz).astimezone(timezone.utc)
    if length > window_end - window_start:
        return []
    busy = _busy(events, ignore_event_id)

    candidates = {window_start + i * FREE_SLOT_STEP for i in range((window_end - window_start) // FREE_SLOT_STEP + 1)}
    candidates.update(e.end for e in busy)
    candidates.update(e.start - length for e in busy)

    def is_free(start: datetime) -> bool:
        end = start + length
        return (window_start <= start and end <= window_end and start > now and start != times.start_at
                and not any(e.start < end and start < e.end for e in busy))

    free = sorted(start for start in candidates if is_free(start))
    before = [s for s in free if s < times.start_at]
    after = [s for s in free if s > times.start_at]
    picks = ([before[-1]] if before else []) + ([after[0]] if after else [])
    return [EventTimes(start_at=s, end_at=s + length) for s in picks]
