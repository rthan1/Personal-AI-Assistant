import json
import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

EDIT_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SCOPES = [EDIT_SCOPE]
_EDIT_SCOPES = {EDIT_SCOPE, "https://www.googleapis.com/auth/calendar"}

# Google can grant a different set than requested (earlier grants via include_granted_scopes, or the user
# unticking a box). oauthlib raises on any difference unless this is set; the granted set is checked below.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class GoogleAuthError(Exception):
    pass


def credentials_from_token(token_json: str | None) -> tuple[Credentials, str | None]:
    """Load (and refresh if needed) credentials. Returns (credentials, new_token_json_if_refreshed)."""
    if not token_json:
        raise GoogleAuthError("Google Calendar is not connected.")

    # Scopes come from the token itself: refreshing with scopes it was never granted fails.
    creds = Credentials.from_authorized_user_info(json.loads(token_json))
    if creds.valid:
        return creds, None
    if not creds.refresh_token:
        raise GoogleAuthError("Google Calendar is not connected.")

    try:
        creds.refresh(Request())
    except RefreshError as exc:
        raise GoogleAuthError("Google Calendar access expired or was revoked.") from exc
    return creds, creds.to_json()


def can_edit_calendar(creds: Credentials) -> bool:
    return bool(_EDIT_SCOPES & set(creds.scopes or []))


def token_json_with_granted_scopes(creds: Credentials) -> str:
    """Token JSON whose scopes are what the user actually granted (to_json saves the requested ones)."""
    token = json.loads(creds.to_json())
    granted = creds.granted_scopes
    if isinstance(granted, str):
        granted = granted.split()
    if granted:
        token["scopes"] = sorted(granted)
    return json.dumps(token)


def _web_flow(client_path: Path, redirect_uri: str, state: str | None = None) -> Flow:
    # The web client is confidential (has a secret), so PKCE is skipped to keep the
    # authorize and callback requests stateless apart from the signed `state`.
    return Flow.from_client_secrets_file(
        str(client_path), scopes=SCOPES, redirect_uri=redirect_uri, state=state, autogenerate_code_verifier=False
    )


def authorization_url(client_path: Path, redirect_uri: str, state: str) -> str:
    url, _ = _web_flow(client_path, redirect_uri, state).authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    return url


def exchange_code(client_path: Path, redirect_uri: str, state: str, code: str) -> str:
    flow = _web_flow(client_path, redirect_uri, state)
    flow.fetch_token(code=code)
    token_json = token_json_with_granted_scopes(flow.credentials)
    if not can_edit_calendar(Credentials.from_authorized_user_info(json.loads(token_json))):
        raise GoogleAuthError("Calendar access wasn't allowed on the Google screen.")
    return token_json
