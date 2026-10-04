"""Registers phone numbers as users of our Photon project.

On Photon's shared-pool plans (Free/Pro) iMessage only routes senders registered as project
users, and each user is assigned a pool number to text. Registering a number only lets it
reach the bot; account sign-up still happens over iMessage with the trusted sender.
"""

import base64
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

SPECTRUM_HOST = "https://spectrum.photon.codes"


class PhotonError(Exception):
    pass


def _users_in(payload: Any) -> list[dict]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        data = data.get("users", [])
    return data if isinstance(data, list) else []


def _user_in(payload: Any) -> dict:
    data = payload.get("user") or payload.get("data") or payload if isinstance(payload, dict) else {}
    if isinstance(data, dict) and isinstance(data.get("user"), dict):
        data = data["user"]
    return data if isinstance(data, dict) else {}


class PhotonUsers:
    def __init__(self, project_id: str, project_secret: str, client: httpx.Client | None = None):
        token = base64.b64encode(f"{project_id}:{project_secret}".encode()).decode()
        self._url = f"{SPECTRUM_HOST}/projects/{project_id}/users/"
        self._headers = {"Authorization": f"Basic {token}"}
        self._client = client or httpx.Client(timeout=30.0)

    def _request(self, method: str, **kwargs) -> Any:
        try:
            response = self._client.request(method, self._url, headers=self._headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Photon %s users failed: %s", method, exc)
            raise PhotonError("Couldn't reach the messaging service. Try again in a minute.") from exc

    def _find(self, phone: str) -> dict | None:
        return next((u for u in _users_in(self._request("GET")) if u.get("phoneNumber") == phone), None)

    def register(self, phone: str) -> str | None:
        """Make sure `phone` (E.164) can text the bot. Returns the number they should text, if Photon assigned one."""
        user = self._find(phone)
        if user is None:
            user = _user_in(self._request("POST", json={"type": "shared", "phoneNumber": phone}))
            if not user.get("assignedPhoneNumber"):
                user = self._find(phone) or user
        return user.get("assignedPhoneNumber") or None
