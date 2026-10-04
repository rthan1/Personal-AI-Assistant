import sqlite3
from dataclasses import dataclass
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken

from assistant.storage.users import _parse_utc

MAX_NOTE_LENGTH = 300
MAX_NOTES_PER_USER = 30


@dataclass(frozen=True)
class Memory:
    id: int
    note: str
    created_at: datetime


def clean_note(note: object) -> str:
    text = " ".join(str(note or "").split())
    if not text:
        raise ValueError("note must not be empty.")
    if len(text) > MAX_NOTE_LENGTH:
        raise ValueError(f"note must be at most {MAX_NOTE_LENGTH} characters. Ask the user for a shorter version.")
    return text


class MemoryRepo:
    """Notes a user asked the assistant to remember. Encrypted at rest; every query is scoped to one user."""

    def __init__(self, conn: sqlite3.Connection, secret_key: str):
        self._conn = conn
        self._fernet = Fernet(secret_key.encode())

    def add(self, user_id: int, note: str) -> Memory:
        text = clean_note(note)
        with self._conn:
            count = self._conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_NOTES_PER_USER:
                raise ValueError(f"Memory is full ({MAX_NOTES_PER_USER} notes). Ask the user which saved note to "
                                 "forget, then try again.")
            encrypted = self._fernet.encrypt(text.encode()).decode()
            cursor = self._conn.execute("INSERT INTO memories (user_id, note) VALUES (?, ?)", (user_id, encrypted))
        row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return self._row_to_memory(row)

    def list(self, user_id: int) -> list[Memory]:
        """The user's notes, newest first. Notes that can't be decrypted (e.g. after a key change) are skipped."""
        rows = self._conn.execute("SELECT * FROM memories WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        memories = []
        for row in rows:
            try:
                memories.append(self._row_to_memory(row))
            except InvalidToken:
                continue
        return memories

    def delete(self, user_id: int, memory_id: int) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id))
        return cursor.rowcount > 0

    def delete_all(self, user_id: int) -> int:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
        return cursor.rowcount

    def _row_to_memory(self, row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            note=self._fernet.decrypt(row["note"].encode()).decode(),
            created_at=_parse_utc(row["created_at"]),
        )
