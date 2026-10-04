"""Nearby places (restaurants, activities) from the Google Places API (New) Text Search."""

import logging
import math
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
SEARCH_FIELD_MASK = ",".join([
    "places.displayName",
    "places.formattedAddress",
    "places.googleMapsUri",
    "places.location",
    "places.rating",
    "places.userRatingCount",
    "places.priceLevel",
    "places.currentOpeningHours.openNow",
    "places.primaryTypeDisplayName",
    "places.businessStatus",
])
LOCATE_FIELD_MASK = "places.location,places.formattedAddress"
PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}
MAX_PAGE_SIZE = 20
# Closed and nameless places are dropped after the search, so ask for a few extra.
EXTRA_RESULTS = 3
MAX_RADIUS_M = 50_000
EARTH_RADIUS_MILES = 3958.8
UNREACHABLE = "Place search isn't reachable right now. Try again in a minute."


class PlacesError(Exception):
    pass


@dataclass(frozen=True)
class LatLng:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Place:
    name: str
    address: str | None
    maps_url: str
    latitude: float | None
    longitude: float | None
    rating: float | None
    rating_count: int | None
    price_level: int | None
    open_now: bool | None
    kind: str | None


class PlaceSource(Protocol):
    def locate(self, address: str) -> LatLng: ...

    def search(self, query: str, center: LatLng, radius_m: int, open_now: bool, min_rating: float | None,
               max_price: int | None, limit: int) -> list[Place]: ...


def build_search_request(query: str, center: LatLng, radius_m: int, open_now: bool, min_rating: float | None,
                         max_price: int | None, limit: int) -> dict:
    body: dict[str, Any] = {
        "textQuery": query,
        "pageSize": min(limit + EXTRA_RESULTS, MAX_PAGE_SIZE),
        "locationBias": {"circle": {
            "center": {"latitude": center.latitude, "longitude": center.longitude},
            "radius": float(min(radius_m, MAX_RADIUS_M)),
        }},
        "languageCode": "en",
    }
    if open_now:
        body["openNow"] = True
    if min_rating is not None:
        body["minRating"] = min_rating
    if max_price is not None:
        body["priceLevels"] = [level for level, value in PRICE_LEVELS.items() if 1 <= value <= max_price]
    return body


def parse_places(body: dict) -> list[Place]:
    places = []
    for raw in body.get("places") or []:
        name = (raw.get("displayName") or {}).get("text")
        maps_url = raw.get("googleMapsUri")
        if not name or not maps_url or raw.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
            continue
        location = raw.get("location") or {}
        places.append(Place(
            name=name,
            address=raw.get("formattedAddress"),
            maps_url=maps_url,
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
            rating=raw.get("rating"),
            rating_count=raw.get("userRatingCount"),
            price_level=PRICE_LEVELS.get(raw.get("priceLevel")),
            open_now=(raw.get("currentOpeningHours") or {}).get("openNow"),
            kind=(raw.get("primaryTypeDisplayName") or {}).get("text"),
        ))
    return places


def distance_miles(a: LatLng, b: LatLng) -> float:
    """Straight-line (haversine) distance."""
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    dlat = lat2 - lat1
    dlng = math.radians(b.longitude - a.longitude)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


class GooglePlacesClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=15.0)

    def locate(self, address: str) -> LatLng:
        body = self._post({"textQuery": address, "pageSize": 1, "languageCode": "en"}, LOCATE_FIELD_MASK)
        try:
            location = body["places"][0]["location"]
            return LatLng(float(location["latitude"]), float(location["longitude"]))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PlacesError("Google Maps couldn't find that address. Ask for a fuller address (street, city).") \
                from exc

    def search(self, query: str, center: LatLng, radius_m: int, open_now: bool, min_rating: float | None,
               max_price: int | None, limit: int) -> list[Place]:
        body = build_search_request(query, center, radius_m, open_now, min_rating, max_price, limit)
        return parse_places(self._post(body, SEARCH_FIELD_MASK))

    def _post(self, body: dict, field_mask: str) -> dict:
        headers = {"X-Goog-Api-Key": self._api_key, "X-Goog-FieldMask": field_mask}
        try:
            response = self._client.post(SEARCH_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            log.warning("Places request failed: %s", exc)
            raise PlacesError(UNREACHABLE) from exc

        if response.status_code == 403:
            log.warning("Places refused request (403): %s", response.text[:500])
            raise PlacesError("Place search isn't enabled on this assistant yet.")
        if 400 <= response.status_code < 500 and response.status_code != 429:
            log.warning("Places rejected request (%s): %s", response.status_code, response.text[:500])
            raise PlacesError("Google Maps couldn't run that search. Try different wording.")
        if response.status_code >= 400:
            log.warning("Places error (%s)", response.status_code)
            raise PlacesError(UNREACHABLE)

        try:
            data = response.json()
        except ValueError as exc:
            log.warning("Unexpected Places response: %s", exc)
            raise PlacesError(UNREACHABLE) from exc
        if not isinstance(data, dict):
            raise PlacesError(UNREACHABLE)
        return data
