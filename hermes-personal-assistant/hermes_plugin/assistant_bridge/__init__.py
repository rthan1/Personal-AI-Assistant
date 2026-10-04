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
            "key": {"type": "string", "enum": ["home_address", "travel_mode", "buffer_minutes", "timezone", "name",
                                               "default_reminder_minutes"]},
            "value": {
                "type": ["string", "number"],
                "description": "travel_mode: drive|transit|walk|bicycle; buffer_minutes: 0-120; "
                               "default_reminder_minutes: 1-1440 for a reminder before every event, or 0 for off",
            },
        },
        required=("key", "value"),
    ),
    _schema(
        "plan_departure",
        "Work out when the user should leave for a calendar event: Google Maps travel time (live traffic) from "
        "origin with their travel mode, arriving their buffer minutes early. Without event_id, uses their next "
        "event today that has a location. With destination and arrive_by but no event_id, plans a trip that isn't "
        "on the calendar.",
        {
            "origin": {
                "type": "string",
                "description": "Where they'll leave from: an address or place, or \"home\" for their saved home "
                               "address. If they haven't said, ask them first.",
            },
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
        {"memory_id": {"type": ["integer", "string"], "description": "Note id, e.g. 3, or \"all\""}},
        required=("memory_id",),
    ),
    _schema(
        "create_event",
        "Propose adding an event to the user's Google Calendar. Saves nothing: returns a pending_change_id to "
        "confirm with confirm_change after the user says yes. Guests can't be added.",
        {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "Local YYYY-MM-DDTHH:MM, or YYYY-MM-DD when all_day"},
            "end": {"type": "string", "description": "Optional; same format as start. For all_day, the last day"},
            "duration_minutes": {"type": "integer", "description": "Optional instead of end; default 60"},
            "all_day": {"type": "boolean", "description": "Optional; true for an all-day event"},
            "location": {"type": "string", "description": "Optional address or place"},
        },
        required=("title", "start"),
    ),
    _schema(
        "update_event",
        "Propose changing an event (id from get_events). Only pass what changes; moving the start keeps its length. "
        "Saves nothing: returns a pending_change_id to confirm with confirm_change after the user says yes.",
        {
            "event_id": {"type": "string"},
            "title": {"type": "string", "description": "Optional new title"},
            "start": {"type": "string", "description": "Optional new local start, YYYY-MM-DDTHH:MM (date for all-day)"},
            "end": {"type": "string", "description": "Optional new local end, same format"},
            "location": {"type": "string", "description": "Optional new location"},
        },
        required=("event_id",),
    ),
    _schema(
        "delete_event",
        "Propose deleting an event (id from get_events). For an invite, it's removed from the user's calendar only. "
        "Saves nothing: returns a pending_change_id to confirm with confirm_change after the user says yes.",
        {"event_id": {"type": "string"}},
        required=("event_id",),
    ),
    _schema(
        "confirm_change",
        "Apply a proposed calendar change. Only call after the user replied yes to that exact change.",
        {"change_id": {"type": "integer", "description": "pending_change_id from create/update/delete_event"}},
        required=("change_id",),
    ),
    _schema(
        "find_places",
        "Find restaurants, cafes, bars, or things to do near a place the user names or one of their events "
        "(Google Maps). Returns up to 3 places with rating, price, open now, distance, and a Google Maps link. Needs "
        "near or event_id; if the user didn't say where, ask first.",
        {
            "query": {"type": "string", "description": "What to look for, e.g. \"ramen\", \"bowling\", \"quiet cafe\""},
            "near": {"type": "string", "description": "Address, neighborhood, city, or landmark to search around"},
            "event_id": {"type": "string", "description": "Search around this event's location (id from get_events)"},
            "open_now": {"type": "boolean", "description": "Only places open right now"},
            "min_rating": {"type": "number", "description": "Optional minimum Google rating, 1-5"},
            "max_price": {"type": "integer", "description": "Optional price cap, 1 ($) to 4 ($$$$)"},
        },
        required=("query",),
    ),
    _schema(
        "set_reminder",
        "Text the user a reminder a set number of minutes before one of their calendar events (timed events only). "
        "The reminder follows the event if it moves and is skipped if it's cancelled.",
        {
            "event_id": {"type": "string", "description": "Event id from get_events"},
            "minutes_before": {"type": "integer", "description": "Minutes before the event starts, 1-1440"},
        },
        required=("event_id", "minutes_before"),
    ),
    _schema(
        "list_reminders",
        "List the user's upcoming reminders (ones they asked for and automatic ones) with their ids.",
    ),
    _schema(
        "cancel_reminder",
        "Cancel one upcoming reminder by id (from list_reminders), or all of them with \"all\".",
        {"reminder_id": {"type": ["integer", "string"], "description": "Reminder id, e.g. 4, or \"all\""}},
        required=("reminder_id",),
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
