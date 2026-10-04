import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

BASE_EVENT_FIELDS = "id,status,summary,start,end,location,recurrence"
# Guest display names are left out: they're free text anyone can set and would reach the model.
SINGLE_EVENT_FIELDS = f"{BASE_EVENT_FIELDS},attendees(email,responseStatus,optional,resource,self),organizer(self)"
EVENT_FIELDS = f"items({BASE_EVENT_FIELDS}),nextPageToken"
MAX_RANGE_DAYS = 31
MAX_EVENT_ID_LENGTH = 1024
ONLINE_LOCATION = re.compile(r"https?://|zoom\.us|meet\.google\.com|teams\.microsoft\.com|webex\.com", re.I)


@dataclass(frozen=True)
class Attendee:
    email: str
    response_status: str = "needsAction"
    optional: bool = False
    resource: bool = False  # a meeting room
    is_self: bool = False


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime  # UTC
    end: datetime  # UTC
    all_day: bool
    location: str | None
    recurring_series: bool = False  # the series itself, not one occurrence
    # Only filled for single-event reads (get_event), not for get_events lists.
    attendees: tuple[Attendee, ...] = ()
    is_organizer: bool = False


class EventSource(Protocol):
    def list_events(self, time_min: datetime, time_max: datetime, page_token: str | None) -> dict[str, Any]: ...

    def get_event(self, event_id: str) -> dict[str, Any]: ...

    def insert_event(self, body: dict[str, Any], send_updates: str = "none") -> dict[str, Any]: ...

    def patch_event(self, event_id: str, body: dict[str, Any], send_updates: str = "none") -> dict[str, Any]: ...

    def delete_event(self, event_id: str) -> None: ...


class GoogleCalendarSource:
    def __init__(self, credentials, calendar_id: str = "primary"):
        from googleapiclient.discovery import build

        self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        self._calendar_id = calendar_id

    def list_events(self, time_min: datetime, time_max: datetime, page_token: str | None) -> dict[str, Any]:
        return (
            self._service.events()
            .list(
                calendarId=self._calendar_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
                pageToken=page_token,
                fields=EVENT_FIELDS,
            )
            .execute()
        )

    def get_event(self, event_id: str) -> dict[str, Any]:
        return (
            self._service.events()
            .get(calendarId=self._calendar_id, eventId=event_id, fields=SINGLE_EVENT_FIELDS)
            .execute()
        )

    # sendUpdates defaults to "none": guests only get emails for invites the user confirmed.
    def insert_event(self, body: dict[str, Any], send_updates: str = "none") -> dict[str, Any]:
        return (
            self._service.events()
            .insert(calendarId=self._calendar_id, body=body, sendUpdates=send_updates, fields=SINGLE_EVENT_FIELDS)
            .execute()
        )

    def patch_event(self, event_id: str, body: dict[str, Any], send_updates: str = "none") -> dict[str, Any]:
        return (
            self._service.events()
            .patch(calendarId=self._calendar_id, eventId=event_id, body=body, sendUpdates=send_updates,
                   fields=SINGLE_EVENT_FIELDS)
            .execute()
        )

    def delete_event(self, event_id: str) -> None:
        self._service.events().delete(calendarId=self._calendar_id, eventId=event_id, sendUpdates="none").execute()


def resolve_date_range(start_date: str | None, end_date: str | None, today: date) -> tuple[date, date]:
    try:
        start = date.fromisoformat(start_date) if start_date else today
        end = date.fromisoformat(end_date) if end_date else start
    except ValueError as exc:
        raise ValueError("Dates must be in YYYY-MM-DD format.") from exc
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"Date range can be at most {MAX_RANGE_DAYS} days.")
    return start, end


def day_bounds_utc(start_date: date, end_date: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC instants covering local start_date 00:00 through the end of end_date (inclusive)."""
    start = datetime.combine(start_date, time.min, tzinfo=tz)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _parse_when(when: dict[str, str], tz: ZoneInfo) -> tuple[datetime, bool]:
    if "dateTime" in when:
        return datetime.fromisoformat(when["dateTime"]).astimezone(timezone.utc), False
    local_midnight = datetime.combine(date.fromisoformat(when["date"]), time.min, tzinfo=tz)
    return local_midnight.astimezone(timezone.utc), True


def parse_event(raw: dict[str, Any], tz: ZoneInfo) -> CalendarEvent | None:
    if raw.get("status") == "cancelled":
        return None
    start, all_day = _parse_when(raw["start"], tz)
    end, _ = _parse_when(raw["end"], tz)
    return CalendarEvent(
        id=raw["id"],
        title=raw.get("summary") or "(no title)",
        start=start,
        end=end,
        all_day=all_day,
        location=(raw.get("location") or "").strip() or None,
        recurring_series=bool(raw.get("recurrence")),
        attendees=tuple(_parse_attendee(a) for a in raw.get("attendees") or [] if a.get("email")),
        is_organizer=bool((raw.get("organizer") or {}).get("self")),
    )


def _parse_attendee(raw: dict[str, Any]) -> Attendee:
    return Attendee(
        email=raw["email"],
        response_status=raw.get("responseStatus") or "needsAction",
        optional=bool(raw.get("optional")),
        resource=bool(raw.get("resource")),
        is_self=bool(raw.get("self")),
    )


def get_events(source: EventSource, start_date: date, end_date: date, tz: ZoneInfo) -> list[CalendarEvent]:
    time_min, time_max = day_bounds_utc(start_date, end_date, tz)
    events: list[CalendarEvent] = []
    page_token = None
    while True:
        page = source.list_events(time_min, time_max, page_token)
        for raw in page.get("items", []):
            event = parse_event(raw, tz)
            if event is not None:
                events.append(event)
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    return sorted(events, key=lambda e: (e.start, not e.all_day))


def get_event(source: EventSource, event_id: str, tz: ZoneInfo) -> CalendarEvent | None:
    """One event by id, or None if it was cancelled. Google raises HttpError 404 for unknown ids."""
    if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > MAX_EVENT_ID_LENGTH:
        raise ValueError("event_id must be an event id from get_events.")
    return parse_event(source.get_event(event_id.strip()), tz)


def is_online_location(location: str | None) -> bool:
    """Meeting links aren't places to travel to (and their passwords shouldn't go to Maps)."""
    return bool(location and ONLINE_LOCATION.search(location))


def display_date(dt: datetime) -> str:
    return f"{dt:%a}, {dt:%b} {dt.day}"


def display_time(dt: datetime) -> str:
    return f"{dt:%I:%M %p}".lstrip("0")


def format_event(event: CalendarEvent, tz: ZoneInfo) -> dict[str, Any]:
    start_local = event.start.astimezone(tz)
    end_local = event.end.astimezone(tz)
    if event.all_day:
        last_day = end_local - timedelta(days=1)
        return {
            "id": event.id,
            "title": event.title,
            "all_day": True,
            "date": display_date(start_local),
            "end_date": display_date(last_day) if last_day.date() != start_local.date() else None,
            "location": event.location,
        }
    return {
        "id": event.id,
        "title": event.title,
        "all_day": False,
        "date": display_date(start_local),
        "start_time": display_time(start_local),
        "end_time": display_time(end_local),
        "end_date": display_date(end_local) if end_local.date() != start_local.date() else None,
        "location": event.location,
        "start_utc": event.start.isoformat(),
        "end_utc": event.end.isoformat(),
    }
