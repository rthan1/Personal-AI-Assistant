import sqlite3

import pytest
from cryptography.fernet import Fernet

from assistant.storage.db import MIGRATIONS, connect
from assistant.storage.memories import MAX_NOTE_LENGTH, MAX_NOTES_PER_USER, MemoryRepo


@pytest.fixture
def ann(repo):
    return repo.upsert("+15551111111", "America/New_York", name="Ann")


@pytest.fixture
def bob(repo):
    return repo.upsert("+15552222222", "America/New_York", name="Bob")


def test_add_returns_saved_note(memories, ann):
    memory = memories.add(ann.id, "Hates early meetings")
    assert memory.note == "Hates early meetings"
    assert memory.created_at.tzinfo is not None


def test_list_returns_newest_first(memories, ann):
    memories.add(ann.id, "first")
    memories.add(ann.id, "second")
    assert [m.note for m in memories.list(ann.id)] == ["second", "first"]


def test_add_collapses_whitespace(memories, ann):
    assert memories.add(ann.id, "  likes \n  window   seats ").note == "likes window seats"


@pytest.mark.parametrize("note", ["", "   ", None])
def test_add_rejects_empty_note(memories, ann, note):
    with pytest.raises(ValueError, match="empty"):
        memories.add(ann.id, note)


def test_add_rejects_too_long_note(memories, ann):
    with pytest.raises(ValueError, match=str(MAX_NOTE_LENGTH)):
        memories.add(ann.id, "x" * (MAX_NOTE_LENGTH + 1))


def test_add_accepts_note_at_max_length(memories, ann):
    assert len(memories.add(ann.id, "x" * MAX_NOTE_LENGTH).note) == MAX_NOTE_LENGTH


def test_add_refuses_when_full_and_keeps_old_notes(memories, ann):
    for i in range(MAX_NOTES_PER_USER):
        memories.add(ann.id, f"note {i}")
    with pytest.raises(ValueError, match="which saved note to forget"):
        memories.add(ann.id, "one too many")
    assert len(memories.list(ann.id)) == MAX_NOTES_PER_USER
    assert memories.list(ann.id)[-1].note == "note 0"


def test_note_limit_is_per_user(memories, ann, bob):
    for i in range(MAX_NOTES_PER_USER):
        memories.add(ann.id, f"note {i}")
    assert memories.add(bob.id, "Bob's note").note == "Bob's note"


def test_note_is_encrypted_at_rest(memories, conn, ann):
    memories.add(ann.id, "allergic to peanuts")
    raw = conn.execute("SELECT note FROM memories").fetchone()[0]
    assert "peanuts" not in raw


def test_notes_from_another_key_are_skipped(conn, memories, ann):
    memories.add(ann.id, "old key note")
    other = MemoryRepo(conn, Fernet.generate_key().decode())
    assert other.list(ann.id) == []


def test_delete_removes_one_note(memories, ann):
    keep = memories.add(ann.id, "keep")
    drop = memories.add(ann.id, "drop")
    assert memories.delete(ann.id, drop.id) is True
    assert [m.id for m in memories.list(ann.id)] == [keep.id]


def test_delete_unknown_id_returns_false(memories, ann):
    assert memories.delete(ann.id, 999) is False


def test_delete_all_returns_count(memories, ann):
    memories.add(ann.id, "a")
    memories.add(ann.id, "b")
    assert memories.delete_all(ann.id) == 2
    assert memories.list(ann.id) == []


def test_user_cannot_list_another_users_notes(memories, ann, bob):
    memories.add(bob.id, "Bob's secret")
    assert memories.list(ann.id) == []


def test_user_cannot_delete_another_users_note_by_id(memories, ann, bob):
    bobs = memories.add(bob.id, "Bob's note")
    assert memories.delete(ann.id, bobs.id) is False
    assert [m.note for m in memories.list(bob.id)] == ["Bob's note"]


def test_delete_all_only_affects_that_user(memories, ann, bob):
    memories.add(ann.id, "Ann's note")
    memories.add(bob.id, "Bob's note")
    memories.delete_all(ann.id)
    assert [m.note for m in memories.list(bob.id)] == ["Bob's note"]


def test_deleting_user_deletes_their_notes(memories, conn, ann):
    memories.add(ann.id, "note")
    with conn:
        conn.execute("DELETE FROM users WHERE id = ?", (ann.id,))
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_migrating_v2_database_keeps_users(tmp_path):
    path = tmp_path / "v2.db"
    raw = sqlite3.connect(path)
    for index in range(2):
        raw.executescript(f"BEGIN; {MIGRATIONS[index]} PRAGMA user_version = {index + 1}; COMMIT;")
    raw.execute("INSERT INTO users (phone, timezone, name) VALUES ('+15551111111', 'UTC', 'Ann')")
    raw.commit()
    raw.close()

    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert conn.execute("SELECT name FROM users").fetchone()[0] == "Ann"
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    conn.close()
