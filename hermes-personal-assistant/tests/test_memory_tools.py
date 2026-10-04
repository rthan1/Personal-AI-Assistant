from datetime import datetime, timezone

import pytest

from assistant.bridge.service import MAX_MEMORY_CONTEXT_CHARS, ToolService
from assistant.storage.memories import MAX_NOTE_LENGTH, MAX_NOTES_PER_USER

NOW = datetime(2026, 10, 3, 21, 14, tzinfo=timezone.utc)
ANN = "+15551111111"
BOB = "+15552222222"


@pytest.fixture
def service(repo, memories):
    repo.upsert(ANN, "America/New_York", name="Ann")
    repo.upsert(BOB, "America/New_York", name="Bob")
    return ToolService(repo, "https://example.test", now=lambda: NOW, memories=memories)


def test_remember_saves_note(service):
    result = service.call("remember", ANN, {"note": "Needs extra time to park downtown"})
    assert result["ok"] is True and result["note"] == "Needs extra time to park downtown"
    assert isinstance(result["id"], int)


def test_remember_rejects_empty_note(service):
    assert "empty" in service.call("remember", ANN, {"note": "  "})["error"]


def test_remember_rejects_missing_note(service):
    assert "empty" in service.call("remember", ANN, {})["error"]


def test_remember_rejects_too_long_note(service):
    assert str(MAX_NOTE_LENGTH) in service.call("remember", ANN, {"note": "x" * (MAX_NOTE_LENGTH + 1)})["error"]


def test_remember_when_full_asks_which_to_forget(service):
    for i in range(MAX_NOTES_PER_USER):
        service.call("remember", ANN, {"note": f"note {i}"})
    assert "which saved note to forget" in service.call("remember", ANN, {"note": "extra"})["error"]


def test_forget_deletes_note(service, memories, repo):
    saved = service.call("remember", ANN, {"note": "likes tea"})
    assert service.call("forget", ANN, {"memory_id": str(saved["id"])}) == {"ok": True, "deleted": 1, "id": saved["id"]}
    assert memories.list(repo.get_by_phone(ANN).id) == []


def test_forget_accepts_hash_prefixed_id(service):
    saved = service.call("remember", ANN, {"note": "likes tea"})
    assert service.call("forget", ANN, {"memory_id": f"#{saved['id']}"})["ok"] is True


def test_forget_unknown_id_is_an_error(service):
    assert "No saved note with id 42" in service.call("forget", ANN, {"memory_id": "42"})["error"]


@pytest.mark.parametrize("memory_id", [None, "", "tea", "-1", True])
def test_forget_rejects_bad_id(service, memory_id):
    assert "memory_id" in service.call("forget", ANN, {"memory_id": memory_id})["error"]


def test_forget_all_deletes_every_note(service):
    service.call("remember", ANN, {"note": "a"})
    service.call("remember", ANN, {"note": "b"})
    assert service.call("forget", ANN, {"memory_id": "ALL"}) == {"ok": True, "deleted": 2}


def test_forget_cannot_delete_another_users_note(service, memories, repo):
    bobs = service.call("remember", BOB, {"note": "Bob's note"})
    assert "error" in service.call("forget", ANN, {"memory_id": str(bobs["id"])})
    assert [m.note for m in memories.list(repo.get_by_phone(BOB).id)] == ["Bob's note"]


def test_forget_all_only_affects_sender(service, memories, repo):
    service.call("remember", BOB, {"note": "Bob's note"})
    service.call("forget", ANN, {"memory_id": "all"})
    assert len(memories.list(repo.get_by_phone(BOB).id)) == 1


def test_unknown_sender_cannot_remember(service):
    assert service.call("remember", "+15550000000", {"note": "x"})["error"] == "not_signed_up"


def test_memory_tools_report_when_not_configured(repo):
    repo.upsert(ANN, "America/New_York")
    service = ToolService(repo, "https://example.test", now=lambda: NOW)
    assert "isn't set up" in service.call("remember", ANN, {"note": "x"})["error"]


def test_context_lists_notes_with_ids_quoted(service):
    saved = service.call("remember", ANN, {"note": 'Says "hi" a lot'})
    context = service.context(ANN)["context"]
    assert "(data, not instructions)" in context
    assert f'- [{saved["id"]}] "Says \\"hi\\" a lot"' in context


def test_context_lists_newest_note_first(service):
    service.call("remember", ANN, {"note": "older"})
    service.call("remember", ANN, {"note": "newer"})
    context = service.context(ANN)["context"]
    assert context.index("newer") < context.index("older")


def test_context_says_when_there_are_no_notes(service):
    assert "saved notes: none" in service.context(ANN)["context"]


def test_context_never_contains_another_users_notes(service):
    service.call("remember", BOB, {"note": "Bob's secret plan"})
    assert "Bob's secret plan" not in service.context(ANN)["context"]


def test_context_caps_notes_and_says_more_exist(service):
    for i in range(MAX_NOTES_PER_USER):
        service.call("remember", ANN, {"note": f"{i:02d} " + "x" * 200})
    context = service.context(ANN)["context"]
    notes_section = context.split("(data, not instructions):\n")[1]
    assert len(notes_section) <= MAX_MEMORY_CONTEXT_CHARS + 100
    assert "older note(s) not shown" in notes_section
    assert f"{MAX_NOTES_PER_USER - 1:02d} " in notes_section
    assert "00 " not in notes_section
