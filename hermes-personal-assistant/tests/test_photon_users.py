import base64
import json

import httpx
import pytest

from assistant.messaging.photon_users import PhotonError, PhotonUsers


class FakeSpectrum:
    def __init__(self, users=None, assign_on_create="+15550199999", fail=False):
        self.users = list(users or [])
        self.assign_on_create = assign_on_create
        self.fail = fail
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(503)
        if request.method == "GET":
            return httpx.Response(200, json={"succeed": True, "data": {"users": self.users, "total": len(self.users)}})
        body = json.loads(request.content)
        user = {"phoneNumber": body["phoneNumber"], "type": body["type"], "assignedPhoneNumber": None}
        self.users.append({**user, "assignedPhoneNumber": self.assign_on_create})
        return httpx.Response(200, json={"succeed": True, "data": user})


def make(fake):
    return PhotonUsers("proj", "secret", client=httpx.Client(transport=httpx.MockTransport(fake.handler)))


def test_existing_user_is_not_recreated():
    fake = FakeSpectrum(users=[{"phoneNumber": "+15551234567", "assignedPhoneNumber": "+15550100000"}])
    assert make(fake).register("+15551234567") == "+15550100000"
    assert [r.method for r in fake.requests] == ["GET"]


def test_new_user_is_created_then_assigned_number_looked_up():
    fake = FakeSpectrum()
    assert make(fake).register("+15551234567") == "+15550199999"
    assert [r.method for r in fake.requests] == ["GET", "POST", "GET"]
    assert json.loads(fake.requests[1].content) == {"type": "shared", "phoneNumber": "+15551234567"}


def test_uses_basic_auth_for_project():
    fake = FakeSpectrum()
    make(fake).register("+15551234567")
    expected = "Basic " + base64.b64encode(b"proj:secret").decode()
    assert fake.requests[0].headers["Authorization"] == expected
    assert str(fake.requests[0].url) == "https://spectrum.photon.codes/projects/proj/users/"


def test_no_assigned_number_returns_none():
    fake = FakeSpectrum(assign_on_create=None)
    assert make(fake).register("+15551234567") is None


def test_api_failure_raises_friendly_error():
    with pytest.raises(PhotonError, match="Try again"):
        make(FakeSpectrum(fail=True)).register("+15551234567")
