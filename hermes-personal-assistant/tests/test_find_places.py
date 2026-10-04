from datetime import datetime, timezone

import pytest

from assistant.bridge import service as service_module
from assistant.bridge.service import ToolService
from assistant.tools.places_service import LatLng, Place, PlacesError
from test_plan_departure import FakeCalendar, event

NOW = datetime(2026, 10, 3, 21, 14, tzinfo=timezone.utc)
ANN = "+15551111111"
BOB = "+15552222222"
CENTER = LatLng(42.28, -83.74)


def place(name, price_level=2, latitude=42.29, longitude=-83.74, **extra):
    fields = {"address": f"{name} St", "maps_url": f"https://maps.google.com/?q={name}", "latitude": latitude,
              "longitude": longitude, "rating": 4.5, "rating_count": 100, "price_level": price_level,
              "open_now": True, "kind": "Restaurant", **extra}
    return Place(name=name, **fields)


class FakePlaces:
    def __init__(self, results=None, error=None):
        self.results = [place("Ramen")] if results is None else results
        self.error = error
        self.located, self.searches = [], []

    def locate(self, address):
        self.located.append(address)
        if self.error:
            raise self.error
        return CENTER

    def search(self, query, center, radius_m, open_now, min_rating, max_price, limit):
        self.searches.append({"query": query, "radius_m": radius_m, "open_now": open_now,
                              "min_rating": min_rating, "max_price": max_price, "limit": limit})
        return self.results


DINNER = event("dinner", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00", "500 Main St, Ann Arbor")


@pytest.fixture
def setup(repo, monkeypatch):
    calendars = {}
    places = FakePlaces()
    monkeypatch.setattr(service_module, "credentials_from_token", lambda token: (token, None))

    def add_user(phone, items=(), home="10 Home Rd", **prefs):
        user = repo.upsert(phone, "America/New_York", home_address=home, **prefs)
        repo.set_google_token(user.id, f"token-{phone}")
        calendars[f"token-{phone}"] = FakeCalendar(list(items))
        return user

    def build(place_source=places):
        return ToolService(repo, "https://example.test", calendar_source_factory=lambda creds: calendars[creds],
                           now=lambda: NOW, places=place_source)

    return add_user, build, places


def test_searches_near_the_named_place(setup):
    add_user, build, places = setup
    add_user(ANN)
    result = build().call("find_places", ANN, {"query": " good   ramen ", "near": "Kerrytown"})
    assert result["searched_near"] == "Kerrytown"
    assert places.located == ["Kerrytown"]
    assert places.searches[0]["query"] == "good ramen"
    assert result["places"][0]["name"] == "Ramen"


def test_searches_around_an_event_location(setup):
    add_user, build, places = setup
    add_user(ANN, [DINNER])
    result = build().call("find_places", ANN, {"query": "dessert", "event_id": "dinner"})
    assert result["searched_near"] == "Dinner"
    assert places.located == ["500 Main St, Ann Arbor"]


def test_event_overrides_near(setup):
    add_user, build, places = setup
    add_user(ANN, [DINNER])
    build().call("find_places", ANN, {"query": "coffee", "event_id": "dinner", "near": "Detroit"})
    assert places.located == ["500 Main St, Ann Arbor"]


def test_without_a_location_asks_where_and_never_uses_home(setup):
    add_user, build, places = setup
    add_user(ANN, home="10 Home Rd")
    result = build().call("find_places", ANN, {"query": "tacos"})
    assert "Ask the user where to search" in result["error"]
    assert places.located == [] and places.searches == []


def test_online_meeting_event_is_refused(setup):
    add_user, build, places = setup
    add_user(ANN, [event("standup", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00",
                         "https://zoom.us/j/123")])
    result = build().call("find_places", ANN, {"query": "lunch", "event_id": "standup"})
    assert "online meeting" in result["error"]
    assert places.located == []


def test_event_without_location_is_refused(setup):
    add_user, build, places = setup
    add_user(ANN, [event("focus", "2026-10-03T20:00:00-04:00", "2026-10-03T21:00:00-04:00")])
    assert "has no location" in build().call("find_places", ANN, {"query": "lunch", "event_id": "focus"})["error"]
    assert places.located == []


def test_unknown_event_points_to_get_events(setup):
    add_user, build, _ = setup
    add_user(ANN)
    assert "get_events" in build().call("find_places", ANN, {"query": "lunch", "event_id": "nope"})["error"]


@pytest.mark.parametrize("mode, radius", [("walk", 3_000), ("bicycle", 8_000), ("transit", 8_000), ("drive", 15_000)])
def test_radius_follows_travel_mode(setup, mode, radius):
    add_user, build, places = setup
    add_user(ANN, travel_mode=mode)
    build().call("find_places", ANN, {"query": "pizza", "near": "Downtown"})
    assert places.searches[0]["radius_m"] == radius


def test_filters_are_passed_on(setup):
    add_user, build, places = setup
    add_user(ANN)
    build().call("find_places", ANN, {"query": "sushi", "near": "Downtown", "open_now": True, "min_rating": 4.3,
                                      "max_price": 2.0})
    assert places.searches[0] == {"query": "sushi", "radius_m": 15_000, "open_now": True, "min_rating": 4.0,
                                  "max_price": 2, "limit": 3}


def test_results_are_capped_at_three(setup):
    add_user, build, _ = setup
    add_user(ANN)
    service = build(FakePlaces([place(f"P{i}") for i in range(6)]))
    assert len(service.call("find_places", ANN, {"query": "food", "near": "Downtown"})["places"]) == 3


def test_result_shows_price_symbols_rounded_distance_and_link(setup):
    add_user, build, _ = setup
    add_user(ANN)
    service = build(FakePlaces([place("Fancy", price_level=4), place("Nowhere", latitude=None, price_level=None)]))
    fancy, nowhere = service.call("find_places", ANN, {"query": "food", "near": "Downtown"})["places"]
    assert fancy["price"] == "$$$$"
    assert fancy["distance_miles"] == 0.7
    assert fancy["maps_url"] == "https://maps.google.com/?q=Fancy"
    assert (nowhere["price"], nowhere["distance_miles"]) == (None, None)


def test_no_results_suggests_broadening(setup):
    add_user, build, _ = setup
    add_user(ANN)
    result = build(FakePlaces([])).call("find_places", ANN, {"query": "unicorn cafe", "near": "Downtown"})
    assert result["places"] == [] and "broader" in result["message"]


@pytest.mark.parametrize("args, message", [
    ({"query": "  "}, "query is required"),
    ({"query": "x" * 101}, "at most 100"),
    ({"query": "food", "max_price": 7}, "max_price"),
    ({"query": "food", "max_price": "cheap"}, "max_price"),
    ({"query": "food", "min_rating": 6}, "min_rating"),
    ({"query": "food", "near": "https://zoom.us/j/1"}, "not a link"),
    ({"query": "food", "near": "x" * 301}, "near must be at most"),
])
def test_bad_arguments_are_rejected(setup, args, message):
    add_user, build, places = setup
    add_user(ANN)
    assert message in build().call("find_places", ANN, {"near": "Downtown", **args})["error"]
    assert places.searches == []


def test_places_errors_are_passed_on(setup):
    add_user, build, _ = setup
    add_user(ANN)
    service = build(FakePlaces(error=PlacesError("Google Maps couldn't find that address.")))
    assert "couldn't find that address" in service.call("find_places", ANN, {"query": "food", "near": "?"})["error"]


def test_not_configured_says_not_set_up(setup):
    add_user, build, _ = setup
    add_user(ANN)
    assert "isn't set up" in build(None).call("find_places", ANN, {"query": "food", "near": "Downtown"})["error"]


def test_unsigned_sender_gets_sign_up_link(setup):
    _, build, places = setup
    result = build().call("find_places", "+15559999999", {"query": "food", "near": "Downtown"})
    assert result["error"] == "not_signed_up"
    assert places.searches == []


def test_cannot_search_around_another_users_event(setup):
    add_user, build, places = setup
    add_user(ANN)
    add_user(BOB, [DINNER])
    result = build().call("find_places", ANN, {"query": "dessert", "event_id": "dinner"})
    assert "get_events" in result["error"]
    assert places.located == []
