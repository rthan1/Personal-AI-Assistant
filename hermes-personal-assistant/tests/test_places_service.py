import json

import httpx
import pytest

from assistant.tools.places_service import (
    SEARCH_FIELD_MASK, SEARCH_URL, GooglePlacesClient, LatLng, PlacesError, build_search_request, distance_miles,
    parse_places,
)

CENTER = LatLng(42.28, -83.74)
RAMEN = {
    "displayName": {"text": "Slurping Turtle"},
    "formattedAddress": "608 E Liberty St, Ann Arbor, MI",
    "googleMapsUri": "https://maps.google.com/?cid=1",
    "location": {"latitude": 42.279, "longitude": -83.741},
    "rating": 4.4,
    "userRatingCount": 1200,
    "priceLevel": "PRICE_LEVEL_MODERATE",
    "currentOpeningHours": {"openNow": True},
    "primaryTypeDisplayName": {"text": "Ramen Restaurant"},
    "businessStatus": "OPERATIONAL",
}


class FakePlaces:
    def __init__(self, status=200, body=None, error=None):
        self.status = status
        self.body = {"places": [RAMEN]} if body is None else body
        self.error = error
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        return httpx.Response(self.status, json=self.body)

    def last_body(self):
        return json.loads(self.requests[-1].content)


def make(fake):
    return GooglePlacesClient("test-key", client=httpx.Client(transport=httpx.MockTransport(fake.handler)))


def search(client, **overrides):
    args = {"query": "ramen", "center": CENTER, "radius_m": 3000, "open_now": False, "min_rating": None,
            "max_price": None, "limit": 3, **overrides}
    return client.search(**args)


def test_search_sends_key_and_field_mask():
    fake = FakePlaces()
    search(make(fake))
    request = fake.requests[0]
    assert str(request.url) == SEARCH_URL
    assert request.headers["X-Goog-Api-Key"] == "test-key"
    assert request.headers["X-Goog-FieldMask"] == SEARCH_FIELD_MASK


def test_search_returns_parsed_places():
    places = search(make(FakePlaces()))
    assert [(p.name, p.price_level, p.open_now, p.kind) for p in places] == [
        ("Slurping Turtle", 2, True, "Ramen Restaurant")]


def test_request_has_location_bias_and_no_optional_filters_by_default():
    body = build_search_request("ramen", CENTER, 3000, False, None, None, 3)
    assert body == {
        "textQuery": "ramen",
        "pageSize": 6,
        "locationBias": {"circle": {"center": {"latitude": 42.28, "longitude": -83.74}, "radius": 3000.0}},
        "languageCode": "en",
    }


def test_request_includes_filters_when_set():
    body = build_search_request("ramen", CENTER, 3000, True, 4.5, 2, 3)
    assert body["openNow"] is True
    assert body["minRating"] == 4.5
    assert body["priceLevels"] == ["PRICE_LEVEL_INEXPENSIVE", "PRICE_LEVEL_MODERATE"]


def test_page_size_and_radius_are_capped():
    body = build_search_request("ramen", CENTER, 90_000, False, None, None, 50)
    assert body["pageSize"] == 20
    assert body["locationBias"]["circle"]["radius"] == 50_000.0


def test_parse_skips_closed_nameless_and_linkless_places():
    body = {"places": [
        {**RAMEN, "businessStatus": "CLOSED_PERMANENTLY"},
        {**RAMEN, "displayName": {}},
        {key: value for key, value in RAMEN.items() if key != "googleMapsUri"},
        {**RAMEN, "displayName": {"text": "Kept"}},
    ]}
    assert [p.name for p in parse_places(body)] == ["Kept"]


def test_parse_tolerates_missing_fields():
    (place,) = parse_places({"places": [{"displayName": {"text": "Bare"}, "googleMapsUri": "https://m/1"}]})
    assert (place.address, place.rating, place.price_level, place.open_now, place.latitude) == (None,) * 5


def test_parse_maps_free_price_level():
    (place,) = parse_places({"places": [{**RAMEN, "priceLevel": "PRICE_LEVEL_FREE"}]})
    assert place.price_level == 0


def test_no_results_is_an_empty_list():
    assert search(make(FakePlaces(body={}))) == []


def test_locate_returns_coordinates():
    fake = FakePlaces(body={"places": [{"location": {"latitude": 40.7, "longitude": -74.0}}]})
    assert make(fake).locate("Times Square") == LatLng(40.7, -74.0)
    assert fake.last_body()["pageSize"] == 1
    assert fake.requests[0].headers["X-Goog-FieldMask"] == "places.location,places.formattedAddress"


def test_locate_without_result_asks_for_fuller_address():
    with pytest.raises(PlacesError, match="fuller address"):
        make(FakePlaces(body={})).locate("nowhere")


@pytest.mark.parametrize("status, message", [
    (403, "isn't enabled"),
    (400, "couldn't run that search"),
    (429, "Try again"),
    (500, "Try again"),
])
def test_http_errors_become_places_errors(status, message):
    with pytest.raises(PlacesError, match=message):
        search(make(FakePlaces(status=status, body={"error": {}})))


def test_network_error_says_try_again():
    with pytest.raises(PlacesError, match="Try again"):
        search(make(FakePlaces(error=httpx.ConnectError("refused"))))


def test_distance_between_known_points():
    # Detroit to Ann Arbor is about 36 miles in a straight line.
    assert distance_miles(LatLng(42.3314, -83.0458), LatLng(42.2808, -83.7430)) == pytest.approx(35.8, abs=0.5)
