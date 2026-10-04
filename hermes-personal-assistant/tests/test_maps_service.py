import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from assistant.tools.maps_service import ROUTES_URL, GoogleRoutesClient, MapsError, parse_duration

NOW = datetime(2026, 10, 3, 21, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 3, 23, 30, tzinfo=timezone.utc)


class FakeRoutes:
    def __init__(self, status=200, body=None, error=None):
        self.status = status
        self.body = {"routes": [{"duration": "1500s"}]} if body is None else body
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
    return GoogleRoutesClient("test-key", client=httpx.Client(transport=httpx.MockTransport(fake.handler)),
                              now=lambda: NOW)


def test_returns_route_duration():
    assert make(FakeRoutes()).get_travel_time("1 Home St", "2 Work Ave", "drive", LATER) == timedelta(minutes=25)


def test_drive_request_uses_traffic_and_departure_time():
    fake = FakeRoutes()
    make(fake).get_travel_time("1 Home St", "2 Work Ave", "drive", LATER)
    request = fake.requests[0]
    assert str(request.url) == ROUTES_URL
    assert request.headers["X-Goog-Api-Key"] == "test-key"
    assert request.headers["X-Goog-FieldMask"] == "routes.duration"
    assert fake.last_body() == {
        "origin": {"address": "1 Home St"},
        "destination": {"address": "2 Work Ave"},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "departureTime": "2026-10-03T23:30:00Z",
    }


@pytest.mark.parametrize("mode, routes_mode", [("transit", "TRANSIT"), ("walk", "WALK"), ("bicycle", "BICYCLE")])
def test_other_modes_have_no_routing_preference(mode, routes_mode):
    fake = FakeRoutes()
    make(fake).get_travel_time("a", "b", mode, LATER)
    body = fake.last_body()
    assert body["travelMode"] == routes_mode
    assert "routingPreference" not in body


def test_departure_about_now_is_left_out():
    fake = FakeRoutes()
    make(fake).get_travel_time("a", "b", "drive", NOW + timedelta(seconds=30))
    assert "departureTime" not in fake.last_body()


def test_unsupported_mode_is_rejected_without_a_request():
    fake = FakeRoutes()
    with pytest.raises(MapsError, match="Unsupported"):
        make(fake).get_travel_time("a", "b", "teleport", LATER)
    assert fake.requests == []


def test_no_route_found():
    with pytest.raises(MapsError, match="no walk route"):
        make(FakeRoutes(body={})).get_travel_time("a", "b", "walk", LATER)


def test_bad_address_is_reported_as_unplannable():
    with pytest.raises(MapsError, match="couldn't plan a route"):
        make(FakeRoutes(status=400, body={"error": {"message": "bad"}})).get_travel_time("a", "b", "drive", LATER)


def test_server_error_says_try_again():
    with pytest.raises(MapsError, match="Try again"):
        make(FakeRoutes(status=503, body={})).get_travel_time("a", "b", "drive", LATER)


def test_network_error_says_try_again():
    with pytest.raises(MapsError, match="Try again"):
        make(FakeRoutes(error=httpx.ConnectError("refused"))).get_travel_time("a", "b", "drive", LATER)


def test_malformed_duration_is_an_error():
    with pytest.raises(MapsError, match="unexpected"):
        make(FakeRoutes(body={"routes": [{"duration": "soon"}]})).get_travel_time("a", "b", "drive", LATER)


def test_parse_duration_accepts_fractional_seconds():
    assert parse_duration("90.5s") == timedelta(seconds=90.5)
