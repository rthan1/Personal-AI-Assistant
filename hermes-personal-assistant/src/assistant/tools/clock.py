from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_current_time(tz_name: str, now: Callable[[], datetime] = utc_now) -> dict:
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {tz_name!r}") from exc

    local = now().astimezone(tz)
    return {
        "timezone": tz_name,
        "iso": local.isoformat(timespec="seconds"),
        "date": local.strftime("%A, %B %d, %Y").replace(" 0", " "),
        "time": local.strftime("%I:%M %p").lstrip("0"),
        "utc_offset": local.strftime("%z"),
    }
