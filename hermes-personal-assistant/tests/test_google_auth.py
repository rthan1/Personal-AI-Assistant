import json
from datetime import datetime, timedelta, timezone

import pytest
from google.oauth2.credentials import Credentials

from assistant.tools.google_auth import (
    EDIT_SCOPE,
    GoogleAuthError,
    can_edit_calendar,
    credentials_from_token,
    token_json_with_granted_scopes,
)

READONLY = "https://www.googleapis.com/auth/calendar.readonly"


def token(scopes):
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"token": "access", "refresh_token": "refresh", "client_id": "id", "client_secret": "secret",
                       "scopes": scopes, "expiry": expiry})


@pytest.mark.parametrize("token_json", [None, ""])
def test_missing_token_reports_not_connected(token_json):
    with pytest.raises(GoogleAuthError, match="not connected"):
        credentials_from_token(token_json)


def test_read_only_token_keeps_its_own_scopes():
    creds, refreshed = credentials_from_token(token([READONLY]))
    assert creds.scopes == [READONLY] and refreshed is None
    assert can_edit_calendar(creds) is False


def test_edit_token_can_edit():
    creds, _ = credentials_from_token(token([EDIT_SCOPE]))
    assert can_edit_calendar(creds) is True


def test_full_calendar_scope_can_edit():
    creds, _ = credentials_from_token(token(["https://www.googleapis.com/auth/calendar"]))
    assert can_edit_calendar(creds) is True


def test_saved_token_uses_granted_scopes_not_requested():
    creds = Credentials("access", refresh_token="refresh", client_id="id", client_secret="secret",
                        scopes=[EDIT_SCOPE], granted_scopes=[READONLY, EDIT_SCOPE])
    assert json.loads(token_json_with_granted_scopes(creds))["scopes"] == sorted([READONLY, EDIT_SCOPE])


def test_saved_token_records_unticked_edit_scope():
    creds = Credentials("access", refresh_token="refresh", client_id="id", client_secret="secret",
                        scopes=[EDIT_SCOPE], granted_scopes=READONLY)
    assert json.loads(token_json_with_granted_scopes(creds))["scopes"] == [READONLY]


def test_saved_token_falls_back_to_requested_scopes():
    creds = Credentials("access", refresh_token="refresh", client_id="id", client_secret="secret",
                        scopes=[EDIT_SCOPE])
    assert json.loads(token_json_with_granted_scopes(creds))["scopes"] == [EDIT_SCOPE]
