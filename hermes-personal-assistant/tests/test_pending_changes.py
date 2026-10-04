import pytest


@pytest.fixture
def ann(repo):
    return repo.upsert("+15551111111", "America/New_York")


@pytest.fixture
def bob(repo):
    return repo.upsert("+15552222222", "America/New_York")


def test_turn_starts_at_zero(pending, ann):
    assert pending.current_turn(ann.id) == 0


def test_start_turn_counts_up(pending, ann):
    assert pending.start_turn(ann.id) == 1
    assert pending.start_turn(ann.id) == 2
    assert pending.current_turn(ann.id) == 2


def test_turns_are_per_user(pending, ann, bob):
    pending.start_turn(ann.id)
    assert pending.current_turn(bob.id) == 0


def test_create_records_current_turn_and_payload(pending, ann):
    pending.start_turn(ann.id)
    change = pending.create(ann.id, "create", {"body": {"summary": "Dentist"}})
    assert (change.action, change.turn, change.payload) == ("create", 1, {"body": {"summary": "Dentist"}})
    assert pending.get(ann.id, change.id) == change


def test_user_cannot_get_another_users_change(pending, ann, bob):
    change = pending.create(bob.id, "delete", {"event_id": "x"})
    assert pending.get(ann.id, change.id) is None


def test_user_cannot_delete_another_users_change(pending, ann, bob):
    change = pending.create(bob.id, "delete", {"event_id": "x"})
    assert pending.delete(ann.id, change.id) is False
    assert pending.get(bob.id, change.id) is not None


def test_delete_removes_change(pending, ann):
    change = pending.create(ann.id, "delete", {"event_id": "x"})
    assert pending.delete(ann.id, change.id) is True
    assert pending.get(ann.id, change.id) is None


def test_expired_change_is_not_returned_and_is_pruned(pending, conn, ann):
    old = pending.create(ann.id, "delete", {"event_id": "x"})
    with conn:
        conn.execute("UPDATE pending_changes SET created_at = '2000-01-01T00:00:00Z'")
    assert pending.get(ann.id, old.id) is None

    pending.create(ann.id, "delete", {"event_id": "y"})
    assert conn.execute("SELECT COUNT(*) FROM pending_changes WHERE id = ?", (old.id,)).fetchone()[0] == 0


def test_deleting_user_deletes_their_changes_and_turns(pending, conn, ann):
    pending.start_turn(ann.id)
    pending.create(ann.id, "delete", {"event_id": "x"})
    with conn:
        conn.execute("DELETE FROM users WHERE id = ?", (ann.id,))
    assert conn.execute("SELECT COUNT(*) FROM pending_changes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0] == 0
