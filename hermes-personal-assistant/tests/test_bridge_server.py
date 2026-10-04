from fastapi.testclient import TestClient

from assistant.bridge.server import create_bridge_app


class RecordingService:
    def __init__(self):
        self.calls = []

    def call(self, tool, sender, args):
        self.calls.append((tool, sender, args))
        return {"ok": True}

    def context(self, sender):
        self.calls.append(("context", sender, None))
        return {"context": "hi"}


def test_requires_bridge_token():
    service = RecordingService()
    client = TestClient(create_bridge_app(service, "s3cret"))

    assert client.post("/tools/get_events", json={"sender": "+15551234567"}).status_code == 401
    assert client.post("/tools/get_events", json={"sender": "+1"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert service.calls == []


def test_forwards_call():
    service = RecordingService()
    client = TestClient(create_bridge_app(service, "s3cret"))

    response = client.post(
        "/tools/get_events",
        json={"sender": "+15551234567", "args": {"start_date": "2026-10-03"}},
        headers={"Authorization": "Bearer s3cret"},
    )

    assert response.json() == {"ok": True}
    assert service.calls == [("get_events", "+15551234567", {"start_date": "2026-10-03"})]


def test_context_requires_bridge_token():
    service = RecordingService()
    client = TestClient(create_bridge_app(service, "s3cret"))
    assert client.post("/context", json={"sender": "+15551234567"}).status_code == 401
    assert service.calls == []


def test_context_forwards_sender():
    service = RecordingService()
    client = TestClient(create_bridge_app(service, "s3cret"))
    response = client.post("/context", json={"sender": "+15551234567"}, headers={"Authorization": "Bearer s3cret"})
    assert response.json() == {"context": "hi"}
    assert service.calls == [("context", "+15551234567", None)]
