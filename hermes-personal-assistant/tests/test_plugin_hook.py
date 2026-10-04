import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

PLUGIN_INIT = Path(__file__).resolve().parents[1] / "hermes_plugin" / "assistant_bridge" / "__init__.py"


@pytest.fixture
def plugin():
    spec = importlib.util.spec_from_file_location("assistant_bridge_under_test", PLUGIN_INIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_returns_bridge_context_for_trusted_sender(plugin, monkeypatch):
    posts = []
    monkeypatch.setattr(plugin, "_sender", lambda: ("+15551234567", None))
    monkeypatch.setattr(plugin, "_post", lambda path, payload, timeout: posts.append((path, payload)) or
                        json.dumps({"context": "Signed-up user"}))

    assert plugin._account_context(user_message="hi", sender_id="+19999999999") == {"context": "Signed-up user"}
    assert posts == [("/context", {"sender": "+15551234567"})]


def test_hook_skips_non_photon_or_group_chats(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_sender", lambda: (None, "group chat"))
    monkeypatch.setattr(plugin, "_post", lambda *a: pytest.fail("should not call bridge"))
    assert plugin._account_context() is None


def test_hook_fails_open_when_bridge_is_down(plugin, monkeypatch):
    def offline(*args):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(plugin, "_sender", lambda: ("+15551234567", None))
    monkeypatch.setattr(plugin, "_post", offline)
    assert plugin._account_context() is None


@pytest.mark.parametrize("tool, args", [
    ("confirm_change", {"change_id": 1}),
    ("confirm_change", {"change_id": 1.0}),
    ("create_event", {"title": "Lunch", "start": "2026-10-06T12:00", "duration_minutes": 60}),
    ("set_preference", {"key": "buffer_minutes", "value": 20}),
    ("set_preference", {"key": "buffer_minutes", "value": 20.0}),
    ("set_preference", {"key": "home_address", "value": "1 Main St, Springfield, IL"}),
    ("forget", {"memory_id": 3}),
    ("forget", {"memory_id": "all"}),
    ("plan_departure", {"origin": "home", "event_id": "abc"}),
    ("find_places", {"query": "ramen", "near": "Ann Arbor", "max_price": 2, "min_rating": 4.5, "open_now": True}),
    ("find_places", {"query": "bowling", "event_id": "abc", "min_rating": 4}),
])
def test_schemas_accept_numbers_the_model_sends(plugin, tool, args):
    """Hermes validates arguments against these schemas and never turns numbers into strings."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = next(s for s in plugin.TOOLS if s["name"] == tool)["parameters"]
    jsonschema.validate(args, schema)


def test_register_adds_tools_and_hook(plugin):
    class Ctx:
        def __init__(self):
            self.tools, self.hooks = [], []

        def register_tool(self, name, **kwargs):
            self.tools.append(name)

        def register_hook(self, name, callback):
            self.hooks.append(name)

    ctx = Ctx()
    plugin.register(ctx)
    assert ctx.tools == ["get_current_time", "get_events", "get_preferences", "set_preference", "plan_departure",
                         "remember", "forget", "create_event", "update_event", "delete_event", "confirm_change", "find_places"]
    assert ctx.hooks == ["pre_llm_call"]
