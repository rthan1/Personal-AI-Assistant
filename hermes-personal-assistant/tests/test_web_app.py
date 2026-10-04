from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from assistant.messaging.photon_users import PhotonError
from assistant.web.app import RateLimiter, create_web_app

BASE_URL = "https://example.test"
REDIRECT_URI = f"{BASE_URL}/oauth/google/callback"
FORM = {"name": "Ann", "timezone": "America/Chicago", "home_address": "1 Main St", "travel_mode": "transit"}


class FakeGoogle:
    def __init__(self):
        self.exchanged = []
        self.fail = False

    def start(self, redirect_uri, state):
        return f"https://accounts.google.test/auth?redirect_uri={redirect_uri}&state={state}"

    def finish(self, redirect_uri, state, code):
        if self.fail:
            raise RuntimeError("scope changed")
        self.exchanged.append((redirect_uri, state, code))
        return f'{{"token": "{code}"}}'


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def client(repo, google):
    app = create_web_app(repo, BASE_URL, "+1 646-579-2852", "America/New_York", google.start, google.finish)
    return TestClient(app, follow_redirects=False)


def submit(client, token, **overrides):
    return client.post("/signup", data={"t": token, **FORM, **overrides})


def state_from(response):
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def test_home_page_links_to_bot(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "sms:+16465792852" in response.text


class FakePhoton:
    def __init__(self, number="+16460000000", fail=False):
        self.number, self.fail, self.registered = number, fail, []

    def register(self, phone):
        if self.fail:
            raise PhotonError("Couldn't reach the messaging service. Try again in a minute.")
        self.registered.append(phone)
        return self.number


def registering_client(repo, google, photon, limiter=None):
    app = create_web_app(repo, BASE_URL, "+1 646-579-2852", "America/New_York", google.start, google.finish,
                         register_phone=photon.register, registration_limiter=limiter)
    return TestClient(app, follow_redirects=False)


def test_home_page_asks_for_phone_when_registration_enabled(repo, google):
    response = registering_client(repo, google, FakePhoton()).get("/")
    assert 'name="phone"' in response.text


def test_start_registers_number_and_shows_assigned_line(repo, google):
    photon = FakePhoton(number="+16460000000")
    response = registering_client(repo, google, photon).post("/start", data={"phone": "(555) 123-4567"})
    assert response.status_code == 200
    assert photon.registered == ["+15551234567"]
    assert "sms:+16460000000" in response.text


def test_start_does_not_create_an_account(repo, google):
    registering_client(repo, google, FakePhoton()).post("/start", data={"phone": "+15551234567"})
    assert repo.get_by_phone("+15551234567") is None


def test_start_falls_back_to_bot_phone_when_no_line_assigned(repo, google):
    response = registering_client(repo, google, FakePhoton(number=None)).post("/start", data={"phone": "+15551234567"})
    assert "sms:+16465792852" in response.text


def test_start_rejects_bad_phone(repo, google):
    photon = FakePhoton()
    response = registering_client(repo, google, photon).post("/start", data={"phone": "123"})
    assert response.status_code == 400 and photon.registered == []


def test_start_shows_error_when_photon_fails(repo, google):
    response = registering_client(repo, google, FakePhoton(fail=True)).post("/start", data={"phone": "+15551234567"})
    assert response.status_code == 400 and "Try again" in response.text


def test_start_is_rate_limited(repo, google):
    photon = FakePhoton()
    client = registering_client(repo, google, photon, limiter=RateLimiter(max_events=1, window_seconds=3600))
    client.post("/start", data={"phone": "+15551111111"})
    response = client.post("/start", data={"phone": "+15552222222"})
    assert response.status_code == 400
    assert photon.registered == ["+15551111111"]


def test_rate_limiter_window_expires():
    now = [0.0]
    limiter = RateLimiter(max_events=1, window_seconds=60, clock=lambda: now[0])
    assert limiter.allow() and not limiter.allow()
    now[0] = 61
    assert limiter.allow()


def test_signup_page_requires_valid_token(client):
    assert client.get("/signup").status_code == 400
    assert client.get("/signup?t=bogus").status_code == 400


def test_signup_page_never_asks_for_phone(repo, client):
    token = repo.create_signup_token("+15551234567")
    response = client.get(f"/signup?t={token}")
    assert response.status_code == 200
    assert 'name="phone"' not in response.text


def test_submit_creates_user_for_token_phone_and_redirects_to_google(repo, client):
    token = repo.create_signup_token("+15551234567")
    response = submit(client, token)

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://accounts.google.test/auth")
    user = repo.get_by_phone("+15551234567")
    assert (user.name, user.timezone, user.home_address, user.travel_mode) == ("Ann", "America/Chicago", "1 Main St", "transit")


def test_submit_with_bad_token_creates_nothing(repo, client):
    assert submit(client, "bogus").status_code == 400
    assert repo._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_submit_rejects_bad_timezone(repo, client):
    token = repo.create_signup_token("+15551234567")
    response = submit(client, token, timezone="Mars/Olympus")
    assert response.status_code == 400
    assert "Unknown timezone" in response.text
    assert repo.get_by_phone("+15551234567") is None


def test_form_values_are_html_escaped(repo, client):
    token = repo.create_signup_token("+15551234567")
    response = submit(client, token, name="<script>x</script>", timezone="bad")
    assert "<script>x</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_token_for_one_phone_cannot_touch_another_user(repo, client):
    bob = repo.upsert("+15552222222", "America/New_York", name="Bob")
    repo.set_google_token(bob.id, "bob-token")
    token = repo.create_signup_token("+15551111111")

    submit(client, token, name="Mallory")

    assert repo.get(bob.id).name == "Bob"
    assert repo.get_google_token(bob.id) == "bob-token"
    assert repo.get_by_phone("+15551111111").name == "Mallory"


def test_callback_saves_token_and_retires_signup_links(repo, client, google):
    token = repo.create_signup_token("+15551234567")
    state = state_from(submit(client, token))

    response = client.get(f"/oauth/google/callback?state={state}&code=abc")

    assert response.status_code == 303 and response.headers["location"] == "/done"
    user = repo.get_by_phone("+15551234567")
    assert repo.get_google_token(user.id) == '{"token": "abc"}'
    assert google.exchanged == [(REDIRECT_URI, state, "abc")]
    assert repo.phone_for_signup_token(token) is None


def test_callback_state_is_single_use(repo, client):
    token = repo.create_signup_token("+15551234567")
    state = state_from(submit(client, token))
    client.get(f"/oauth/google/callback?state={state}&code=abc")
    assert client.get(f"/oauth/google/callback?state={state}&code=evil").status_code == 400


def test_callback_rejects_unknown_state(repo, client, google):
    assert client.get("/oauth/google/callback?state=bogus&code=abc").status_code == 400
    assert google.exchanged == []


def test_callback_when_user_denies_access(repo, client, google):
    token = repo.create_signup_token("+15551234567")
    state = state_from(submit(client, token))

    response = client.get(f"/oauth/google/callback?state={state}&error=access_denied")

    assert response.status_code == 400 and "cancelled" in response.text
    assert repo.get_by_phone("+15551234567").google_connected is False
    assert google.exchanged == []


def test_callback_when_exchange_fails(repo, client, google):
    google.fail = True
    token = repo.create_signup_token("+15551234567")
    state = state_from(submit(client, token))

    response = client.get(f"/oauth/google/callback?state={state}&code=abc")

    assert response.status_code == 502
    assert repo.get_by_phone("+15551234567").google_connected is False
