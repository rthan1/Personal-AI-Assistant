"""Hermes plugin: relays assistant tool calls to the local assistant app.

The sender's phone number comes from the gateway's session context (set by the Photon
adapter), never from tool arguments, so the model cannot act as another user.
"""

import json
import os
import urllib.error
import urllib.request

BRIDGE_URL = os.getenv("ASSISTANT_BRIDGE_URL", "http://127.0.0.1:8788")
TOOLSET = "assistant"
TIMEOUT_SECONDS = 30
CONTEXT_TIMEOUT_SECONDS = 5


def _schema(name, description, properties=None, required=()):
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": list(required)},
    }


_DATE = {"type": "string", "description": "Local date, YYYY-MM-DD"}

TOOLS = [
    _schema(
        "get_current_time",
        "Get the current date and time in the user's timezone (or another IANA timezone).",
        {"timezone": {"type": "string", "description": "Optional IANA timezone, e.g. America/Los_Angeles"}},
    ),
    _schema(
        "get_events",
        "List events on the user's Google Calendar for a local date range (inclusive). Defaults to today.",
        {"start_date": _DATE, "end_date": _DATE},
    ),
    _schema(
        "get_preferences",
        "Get the user's saved profile: name, timezone, home address, travel mode, buffer minutes, calendar connected.",
    ),
    _schema(
        "set_preference",
        "Save one user preference.",
        {
            "key": {"type": "string", "enum": ["home_address", "travel_mode", "buffer_minutes", "timezone", "name"]},
            "value": {"type": "string", "description": "travel_mode: drive|transit|walk|bicycle; buffer_minutes: 0-120"},
        },
        required=("key", "value"),
    ),
    _schema(
        "plan_departure",
        "Work out when the user should leave for a calendar event: Google Maps travel time (live traffic) from their "
        "saved home address with their travel mode, arriving their buffer minutes early. Without event_id, uses "
        "their next event today that has a location. With destination and arrive_by but no event_id, plans a trip "
        "that isn't on the calendar.",
        {
            "event_id": {"type": "string", "description": "Optional event id from get_events"},
            "destination": {"type": "string", "description": "Optional address, used only if the event has no location"},
            "arrive_by": {
                "type": "string",
                "description": "Optional local time the user wants to get there, YYYY-MM-DDTHH:MM. Required for "
                               "all-day events; overrides the event start when the user names a time.",
            },
        },
    ),
    _schema(
        "remember",
        "Save a short note about the user that you'll see in every future conversation with them. Use when they ask "
        "you to remember something, or clearly state a lasting preference about themselves. Not for settings that "
        "set_preference handles, calendar content, or secrets.",
        {"note": {"type": "string", "description": "The fact to remember, in your words, max 300 characters"}},
        required=("note",),
    ),
    _schema(
        "forget",
        "Delete one saved note by its id (ids are in the account status note), or all of them with \"all\".",
        {"memory_id": {"type": "string", "description": "Note id, e.g. \"3\", or \"all\""}},
        required=("memory_id",),
    ),
]


def _sender():
    from gateway.session_context import get_session_env

    if get_session_env("HERMES_SESSION_PLATFORM") != "photon":
        return None, "Assistant tools are only available over iMessage."
    if get_session_env("HERMES_SESSION_CHAT_TYPE") != "dm":
        return None, "Assistant tools only work in a direct message, not group chats."
    sender = get_session_env("HERMES_SESSION_USER_ID")
    if not sender:
        return None, "Could not identify the sender."
    return sender, None


def _post(path, payload, timeout):
    request = urllib.request.Request(
        f"{BRIDGE_URL}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('ASSISTANT_BRIDGE_TOKEN', '')}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode()


def _make_handler(tool_name):
    def handler(args, **kwargs):
        sender, problem = _sender()
        if problem:
            return json.dumps({"error": problem})
        try:
            return _post(f"/tools/{tool_name}", {"sender": sender, "args": args or {}}, TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            return json.dumps({"error": f"Assistant service returned HTTP {exc.code}."})
        except (urllib.error.URLError, TimeoutError):
            return json.dumps({"error": "The assistant service is offline right now. Try again in a bit."})

    return handler


def _account_context(**kwargs):
    """pre_llm_call hook: tell the model who is texting (or hand it their sign-up link). Fails open."""
    try:
        sender, problem = _sender()
        if problem:
            return None
        context = json.loads(_post("/context", {"sender": sender}, CONTEXT_TIMEOUT_SECONDS)).get("context")
        return {"context": context} if context else None
    except Exception:
        return None


def register(ctx):
    for schema in TOOLS:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=_make_handler(schema["name"]),
            description=schema["description"],
        )
    ctx.register_hook("pre_llm_call", _account_context)
