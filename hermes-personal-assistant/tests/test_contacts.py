from datetime import date

import pytest
from cryptography.fernet import Fernet

from assistant.storage.contacts import MAX_CONTACTS_PER_USER, Contact, ContactRepo

TODAY = date(2026, 10, 4)


@pytest.fixture
def ann(repo):
    return repo.upsert("+15551111111", "America/New_York", name="Ann")


@pytest.fixture
def bob(repo):
    return repo.upsert("+15552222222", "America/New_York", name="Bob")


def test_save_and_get_ignores_case_and_spacing(contacts, ann):
    saved = contacts.save(ann.id, "Sam Lee", "sam@x.com")
    assert contacts.get(ann.id, "  sam   LEE ") == Contact(saved.id, "Sam Lee", "sam@x.com")


def test_saving_same_name_updates_email(contacts, ann):
    contacts.save(ann.id, "Sam", "old@x.com")
    contacts.save(ann.id, "sam", "new@x.com")
    assert [(c.name, c.email) for c in contacts.list(ann.id)] == [("Sam", "new@x.com")]


def test_list_sorted_by_name(contacts, ann):
    contacts.save(ann.id, "zoe", "z@x.com")
    contacts.save(ann.id, "Alex", "a@x.com")
    assert [c.name for c in contacts.list(ann.id)] == ["Alex", "zoe"]


def test_encrypted_at_rest(contacts, conn, ann):
    contacts.save(ann.id, "Sam", "sam@x.com")
    row = conn.execute("SELECT name, email FROM contacts").fetchone()
    assert "Sam" not in row["name"] and "sam@x.com" not in row["email"]


def test_rows_from_another_key_are_skipped(contacts, conn, ann):
    contacts.save(ann.id, "Sam", "sam@x.com")
    assert ContactRepo(conn, Fernet.generate_key().decode()).list(ann.id) == []


def test_cap_per_user(contacts, ann, bob):
    for i in range(MAX_CONTACTS_PER_USER):
        contacts.save(ann.id, f"p{i}", f"p{i}@x.com")
    with pytest.raises(ValueError, match="full"):
        contacts.save(ann.id, "one more", "m@x.com")
    contacts.save(ann.id, "p0", "changed@x.com")
    assert contacts.save(bob.id, "Sam", "sam@x.com").email == "sam@x.com"


def test_delete_and_delete_all(contacts, ann):
    contacts.save(ann.id, "Sam", "sam@x.com")
    contacts.save(ann.id, "Alex", "a@x.com")
    assert contacts.delete(ann.id, "SAM") is True and contacts.delete(ann.id, "Sam") is False
    assert contacts.delete_all(ann.id) == 1 and contacts.list(ann.id) == []


def test_users_cannot_see_or_delete_each_others_contacts(contacts, ann, bob):
    contacts.save(bob.id, "Sam", "bobs-sam@x.com")
    assert contacts.get(ann.id, "Sam") is None and contacts.list(ann.id) == []
    assert contacts.delete(ann.id, "Sam") is False
    contacts.delete_all(ann.id)
    assert contacts.get(bob.id, "Sam").email == "bobs-sam@x.com"


def test_deleting_user_deletes_contacts_and_counts(contacts, conn, ann):
    contacts.save(ann.id, "Sam", "sam@x.com")
    contacts.record_invites(ann.id, TODAY, 2)
    with conn:
        conn.execute("DELETE FROM users WHERE id = ?", (ann.id,))
    assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM invite_counts").fetchone()[0] == 0


def test_invite_counts_add_up_per_user_and_day(contacts, ann, bob):
    contacts.record_invites(ann.id, TODAY, 2)
    contacts.record_invites(ann.id, TODAY, 3)
    assert contacts.invites_sent(ann.id, TODAY) == 5
    assert contacts.invites_sent(bob.id, TODAY) == 0
    assert contacts.invites_sent(ann.id, date(2026, 10, 5)) == 0


def test_old_invite_counts_are_pruned(contacts, conn, ann):
    contacts.record_invites(ann.id, date(2026, 10, 3), 4)
    contacts.record_invites(ann.id, TODAY, 1)
    assert conn.execute("SELECT day FROM invite_counts").fetchall()[0]["day"] == "2026-10-04"
