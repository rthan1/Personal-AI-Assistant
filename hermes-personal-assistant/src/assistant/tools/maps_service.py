"""Travel time between two addresses from the Google Routes API."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

import httpx

from assistant.tools import clock

log = logging.getLogger(__name__)

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.duration"
ROUTES_TRAVEL_MODES = {"drive": "DRIVE", "transit": "TRANSIT", "walk": "WALK", "bicycle": "BICYCLE"}
# Routes rejects departure times in the past, and a time "now" can be past by the time it arrives.
MIN_DEPARTURE_LEAD = timedelta(minutes=1)


class MapsError(Exception):
    pass


class TravelTimeSource(Protocol):
    def get_travel_time(self, origin: str, destination: str, mode: str, depart_at: datetime) -> timedelta: ...


def parse_duration(text: str) -> timedelta:
    """Routes durations look like "1500s"."""
    if not isinstance(text, str) or not text.endswith("s"):
        raise ValueError(f"Unexpected duration {text!r}")
    return timedelta(seconds=float(text[:-1]))


def build_request(origin: str, destination: str, mode: str, depart_at: datetime | None) -> dict:
    if mode not in ROUTES_TRAVEL_MODES:
        raise MapsError(f"Unsupported travel mode {mode!r}.")
    body: dict = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": ROUTES_TRAVEL_MODES[mode],
    }
    if mode == "drive":
        body["routingPreference"] = "TRAFFIC_AWARE"
    if depart_at is not None:
        body["departureTime"] = depart_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return body


class GoogleRoutesClient:
    def __init__(
        self,
        api_key: str,
        client: httpx.Client | None = None,
        now: Callable[[], datetime] = clock.utc_now,
    ):
        self._headers = {"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": FIELD_MASK}
        self._client = client or httpx.Client(timeout=15.0)
        self._now = now

    def get_travel_time(self, origin: str, destination: str, mode: str, depart_at: datetime) -> timedelta:
        # Leaving (about) now: let Routes use the current time rather than send a time it may consider past.
        departure = depart_at if depart_at > self._now() + MIN_DEPARTURE_LEAD else None
        body = build_request(origin, destination, mode, departure)
        try:
            response = self._client.post(ROUTES_URL, headers=self._headers, json=body)
        except httpx.HTTPError as exc:
            log.warning("Routes request failed: %s", exc)
            raise MapsError("Google Maps isn't reachable right now. Try again in a minute.") from exc

        if 400 <= response.status_code < 500:
            log.warning("Routes rejected request (%s): %s", response.status_code, response.text[:500])
            raise MapsError("Google Maps couldn't plan a route between those places. Check that both addresses "
                            "are complete (street, city).")
        if response.status_code >= 400:
            log.warning("Routes server error (%s)", response.status_code)
            raise MapsError("Google Maps isn't reachable right now. Try again in a minute.")

        try:
            routes = response.json().get("routes") or []
            if not routes:
                raise MapsError(f"Google Maps found no {mode} route between those places.")
            return parse_duration(routes[0]["duration"])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("Unexpected Routes response: %s", exc)
            raise MapsError("Google Maps returned an unexpected answer. Try again in a minute.") from exc
