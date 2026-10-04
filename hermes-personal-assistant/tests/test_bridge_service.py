from datetime import datetime, timezone

import pytest

from assistant.bridge import service as service_module
from assistant.bridge.service import ToolService

NOW = datetime(2026, 10, 3, 21, 14, tzinfo=timezone.utc)
BASE_URL = "https://example.test"
SIGNUP = f"{BASE_URL}/signup?t="


class FakeSource:
    def __init__(self, items):
        self.items = items

    def list_events(self, time_min, time_max, page_token):
        return {"items": self.items}


@pytest.fixture
def make_service(repo, monkeypatch):
    sources = {}

    def fake_credentials(token_json):
        if not token_json:
            raise service_module.GoogleAuthError("Google Calendar is not connected.")
        return token_json, None

    monkeypatch.setattr(service_module, "credentials_from_token", fake_credentials)

    def build():
        return ToolService(repo, BASE_URL, calendar_source_factory=lambda creds: sources[creds], now=lambda: NOW)

    return build, sources


def test_unknown_sender_gets_signup_link(repo, make_service):
    build, _ = make_service
    result = build().call("get_current_time", "+15550000000", {})
    assert result["error"] == "not_signed_up"
    assert SIGNUP in result["message"]


def test_signup_link_is_tied_to_sender_phone(repo, make_service):
    build, _ = make_service
    message = build().call("get_current_time", "(555) 000-0000", {})["message"]
    token = message.split("?t=")[1].split()[0]
    assert repo.phone_for_signup_token(token) == "+15550000000"


def test_unparseable_sender_gets_no_signup_link(make_service):
    build, _ = make_service
    result = build().call("get_current_time", "someone@icloud.com", {})
    assert result["error"] == "not_signed_up"
    assert SIGNUP not in result["message"]


def test_unknown_tool(repo, make_service):
    build, _ = make_service
    repo.upsert("+15551234567", "America/New_York")
    assert "Unknown tool" in build().call("delete_everything", "+15551234567", {})["error"]


def test_current_time_uses_user_timezone(repo, make_service):
    build, _ = make_service
    repo.upsert("+15551234567", "America/Los_Angeles")
    assert build().call("get_current_time", "(555) 123-4567", {})["time"] == "2:14 PM"


def test_each_user_only_sees_their_own_calendar(repo, make_service):
    build, sources = make_service
    ann = repo.upsert("+15551111111", "America/New_York")
    bob = repo.upsert("+15552222222", "America/New_York")
    repo.set_google_token(ann.id, "ann-token")
    repo.set_google_token(bob.id, "bob-token")
    event = lambda title: {"id": title, "summary": title, "start": {"dateTime": "2026-10-03T15:00:00-04:00"},
                           "end": {"dateTime": "2026-10-03T16:00:00-04:00"}}
    sources["ann-token"] = FakeSource([event("Ann's dentist")])
    sources["bob-token"] = FakeSource([event("Bob's gym")])

    service = build()
    ann_events = service.call("get_events", "+15551111111", {})["events"]
    bob_events = service.call("get_events", "+15552222222", {})["events"]

    assert [e["title"] for e in ann_events] == ["Ann's dentist"]
    assert [e["title"] for e in bob_events] == ["Bob's gym"]


def test_get_events_without_google_prompts_reconnect(repo, make_service):
    build, _ = make_service
    repo.upsert("+15551234567", "America/New_York")
    result = build().call("get_events", "+15551234567", {})
    assert "not connected" in result["error"] and SIGNUP in result["error"]
    token = result["error"].split("?t=")[1].split()[0]
    assert repo.phone_for_signup_token(token) == "+15551234567"


def test_set_and_get_preferences(repo, make_service):
    build, _ = make_service
    repo.upsert("+15551234567", "America/New_York")
    service = build()

    assert service.call("set_preference", "+15551234567", {"key": "buffer_minutes", "value": "20"}) == {
        "ok": True, "key": "buffer_minutes", "value": 20}
    prefs = service.call("get_preferences", "+15551234567", {})
    assert prefs["buffer_minutes"] == 20
    assert "id" not in prefs and "google_token" not in prefs


def test_set_preference_rejects_bad_values(repo, make_service):
    build, _ = make_service
    repo.upsert("+15551234567", "America/New_York")
    service = build()
    assert "error" in service.call("set_preference", "+15551234567", {"key": "phone", "value": "+15550000000"})
    assert "error" in service.call("set_preference", "+15551234567", {"key": "buffer_minutes", "value": "999"})


def test_context_for_unknown_sender_has_their_signup_link(repo, make_service):
    build, _ = make_service
    context = build().context("+15550000000")["context"]
    assert "NOT signed up" in context
    token = context.split("?t=")[1].split()[0]
    assert repo.phone_for_signup_token(token) == "+15550000000"


def test_context_for_known_user_shows_profile_and_local_time(repo, make_service):
    build, _ = make_service
    user = repo.upsert("+15551234567", "America/Los_Angeles", name="Ann")
    repo.set_google_token(user.id, "token")
    context = build().context("+15551234567")["context"]
    assert '"Ann"' in context
    assert "2:14 PM" in context
    assert "Google Calendar connected: yes" in context
    assert SIGNUP not in context


def test_context_without_calendar_includes_connect_link(repo, make_service):
    build, _ = make_service
    repo.upsert("+15551234567", "America/New_York", name="Ann")
    context = build().context("+15551234567")["context"]
    assert "Google Calendar connected: no" in context and SIGNUP in context


def test_context_only_describes_the_sender(repo, make_service):
    build, _ = make_service
    repo.upsert("+15551111111", "America/New_York", name="Ann")
    repo.upsert("+15552222222", "America/New_York", name="Bob")
    context = build().context("+15551111111")["context"]
    assert "Ann" in context and "Bob" not in context


def test_bad_argument_names_return_error(repo, make_service):
    build, _ = make_service
    repo.upsert("+15551234567", "America/New_York")
    result = build().call("get_events", "+15551234567", {"start_date": "tomorrow"})
    assert "YYYY-MM-DD" in result["error"]
