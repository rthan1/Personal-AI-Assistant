import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TRAVEL_MODES = ("drive", "transit", "walk", "bicycle")
MAX_BUFFER_MINUTES = 120
MAX_ADDRESS_LENGTH = 300
MAX_NAME_LENGTH = 80

SETTABLE_PREFERENCES = ("home_address", "travel_mode", "buffer_minutes", "timezone", "name")


def normalize_phone(raw: str) -> str:
    """Normalize to E.164. Bare 10-digit numbers are assumed to be US/Canada."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    raise ValueError("Enter a valid phone number, e.g. +1 555 123 4567.")


def validate_preference(key: str, value: Any) -> Any:
    if key not in SETTABLE_PREFERENCES:
        raise ValueError(f"Unknown preference {key!r}. Allowed: {', '.join(SETTABLE_PREFERENCES)}.")

    if key == "travel_mode":
        mode = str(value).strip().lower()
        mode = {"driving": "drive", "car": "drive", "walking": "walk", "bike": "bicycle", "biking": "bicycle",
                "cycling": "bicycle", "public transit": "transit", "train": "transit", "bus": "transit"}.get(mode, mode)
        if mode not in TRAVEL_MODES:
            raise ValueError(f"travel_mode must be one of: {', '.join(TRAVEL_MODES)}.")
        return mode

    if key == "buffer_minutes":
        try:
            minutes = int(str(value).strip())
        except ValueError as exc:
            raise ValueError("buffer_minutes must be a whole number of minutes.") from exc
        if not 0 <= minutes <= MAX_BUFFER_MINUTES:
            raise ValueError(f"buffer_minutes must be between 0 and {MAX_BUFFER_MINUTES}.")
        return minutes

    if key == "timezone":
        tz = str(value).strip()
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown timezone {tz!r}; use an IANA name like America/New_York.") from exc
        return tz

    text = " ".join(str(value).split())
    limit = MAX_ADDRESS_LENGTH if key == "home_address" else MAX_NAME_LENGTH
    if not text:
        raise ValueError(f"{key} cannot be empty.")
    if len(text) > limit:
        raise ValueError(f"{key} must be at most {limit} characters.")
    return text
