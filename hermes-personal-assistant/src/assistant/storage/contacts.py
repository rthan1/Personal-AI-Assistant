import sqlite3
from dataclasses import dataclass
from datetime import date

from cryptography.fernet import Fernet, InvalidToken

MAX_CONTACTS_PER_USER = 100


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    email: str


def name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


class ContactRepo:
    """People a user invites by name, and how many invites they sent per day.

    Names and emails are encrypted at rest, so lookups decrypt the user's (few) contacts. Every query is scoped to
    one user. Callers validate names and emails first.
    """

    def __init__(self, conn: sqlite3.Connection, secret_key: str):
        self._conn = conn
        self._fernet = Fernet(secret_key.encode())

    def save(self, user_id: int, name: str, email: str) -> Contact:
        """Adds the contact, or updates the email of the one with the same name."""
        existing = self.get(user_id, name)
        with self._conn:
            if existing is not None:
                self._conn.execute("UPDATE contacts SET email = ? WHERE id = ? AND user_id = ?",
                                   (self._encrypt(email), existing.id, user_id))
                return Contact(existing.id, existing.name, email)
            count = self._conn.execute("SELECT COUNT(*) FROM contacts WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_CONTACTS_PER_USER:
                raise ValueError(f"Contacts are full ({MAX_CONTACTS_PER_USER}). Ask the user which one to forget.")
            cursor = self._conn.execute("INSERT INTO contacts (user_id, name, email) VALUES (?, ?, ?)",
                                        (user_id, self._encrypt(name), self._encrypt(email)))
        return Contact(cursor.lastrowid, name, email)

    def get(self, user_id: int, name: str) -> Contact | None:
        key = name_key(name)
        return next((c for c in self.list(user_id) if name_key(c.name) == key), None)

    def list(self, user_id: int) -> list[Contact]:
        """The user's contacts by name. Rows that can't be decrypted (e.g. after a key change) are skipped."""
        rows = self._conn.execute("SELECT * FROM contacts WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
        contacts = []
        for row in rows:
            try:
                contacts.append(Contact(row["id"], self._decrypt(row["name"]), self._decrypt(row["email"])))
            except InvalidToken:
                continue
        return sorted(contacts, key=lambda c: name_key(c.name))

    def delete(self, user_id: int, name: str) -> bool:
        contact = self.get(user_id, name)
        if contact is None:
            return False
        with self._conn:
            self._conn.execute("DELETE FROM contacts WHERE id = ? AND user_id = ?", (contact.id, user_id))
        return True

    def delete_all(self, user_id: int) -> int:
        with self._conn:
            return self._conn.execute("DELETE FROM contacts WHERE user_id = ?", (user_id,)).rowcount

    def invites_sent(self, user_id: int, day: date) -> int:
        row = self._conn.execute("SELECT count FROM invite_counts WHERE user_id = ? AND day = ?",
                                 (user_id, day.isoformat())).fetchone()
        return row["count"] if row else 0

    def record_invites(self, user_id: int, day: date, count: int) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO invite_counts (user_id, day, count) VALUES (?, ?, ?) "
                "ON CONFLICT (user_id, day) DO UPDATE SET count = count + excluded.count",
                (user_id, day.isoformat(), count),
            )
            self._conn.execute("DELETE FROM invite_counts WHERE user_id = ? AND day < ?",
                               (user_id, day.isoformat()))

    def _encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode()).decode()

    def _decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
