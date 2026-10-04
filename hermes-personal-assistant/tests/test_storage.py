import sqlite3

import pytest

from assistant.storage.db import MIGRATIONS, connect


def test_migrations_set_user_version():
    conn = connect(":memory:")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_reconnect_does_not_rerun_migrations(tmp_path):
    path = tmp_path / "a.db"
    connect(path).close()
    connect(path).close()


def test_upsert_creates_then_updates(repo):
    user = repo.upsert("+15551234567", "America/New_York", name="Ann", home_address="1 Main St")
    assert (user.name, user.home_address, user.travel_mode, user.buffer_minutes) == ("Ann", "1 Main St", "drive", 10)
    assert user.google_connected is False

    again = repo.upsert("+15551234567", "America/Chicago", home_address="2 Oak Ave")
    assert again.id == user.id
    assert (again.timezone, again.home_address, again.name) == ("America/Chicago", "2 Oak Ave", "Ann")


def test_phone_is_unique(repo):
    repo.upsert("+15551234567", "America/New_York")
    with pytest.raises(sqlite3.IntegrityError):
        with repo._conn:
            repo._conn.execute("INSERT INTO users (phone, timezone) VALUES ('+15551234567', 'UTC')")


def test_update_rejects_unknown_columns(repo):
    user = repo.upsert("+15551234567", "America/New_York")
    with pytest.raises(ValueError, match="Unknown preference"):
        repo.update_preferences(user.id, google_token="x")


def test_google_token_encrypted_round_trip(repo):
    user = repo.upsert("+15551234567", "America/New_York")
    repo.set_google_token(user.id, '{"refresh_token": "secret"}')

    raw = repo._conn.execute("SELECT google_token FROM users WHERE id = ?", (user.id,)).fetchone()[0]
    assert "secret" not in raw
    assert repo.get_google_token(user.id) == '{"refresh_token": "secret"}'
    assert repo.get(user.id).google_connected is True

    repo.set_google_token(user.id, None)
    assert repo.get_google_token(user.id) is None


def test_oauth_state_single_use(repo):
    user = repo.upsert("+15551234567", "America/New_York")
    state = repo.create_oauth_state(user.id)
    assert repo.consume_oauth_state(state) == user.id
    assert repo.consume_oauth_state(state) is None
    assert repo.consume_oauth_state("bogus") is None


def test_oauth_state_expires(repo):
    user = repo.upsert("+15551234567", "America/New_York")
    state = repo.create_oauth_state(user.id)
    with repo._conn:
        repo._conn.execute("UPDATE oauth_states SET created_at = '2000-01-01T00:00:00Z'")
    assert repo.consume_oauth_state(state) is None


def test_signup_token_maps_to_phone(repo):
    token = repo.create_signup_token("+15551234567")
    assert repo.phone_for_signup_token(token) == "+15551234567"
    assert repo.phone_for_signup_token(token) == "+15551234567"
    assert repo.phone_for_signup_token("bogus") is None


def test_signup_token_expires_and_is_pruned(repo):
    old = repo.create_signup_token("+15551234567")
    with repo._conn:
        repo._conn.execute("UPDATE signup_tokens SET created_at = '2000-01-01T00:00:00Z'")
    assert repo.phone_for_signup_token(old) is None

    repo.create_signup_token("+15559999999")
    assert repo._conn.execute("SELECT COUNT(*) FROM signup_tokens WHERE token = ?", (old,)).fetchone()[0] == 0


def test_delete_signup_tokens_only_affects_that_phone(repo):
    mine = repo.create_signup_token("+15551111111")
    theirs = repo.create_signup_token("+15552222222")
    repo.delete_signup_tokens("+15551111111")
    assert repo.phone_for_signup_token(mine) is None
    assert repo.phone_for_signup_token(theirs) == "+15552222222"
