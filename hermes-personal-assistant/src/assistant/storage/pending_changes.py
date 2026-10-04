import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from assistant.storage.users import _parse_utc, _utc_cutoff

PENDING_CHANGE_MAX_AGE = timedelta(minutes=10)


@dataclass(frozen=True)
class PendingChange:
    id: int
    action: str
    payload: dict[str, Any]
    turn: int
    created_at: datetime


class PendingChangeRepo:
    """Calendar changes waiting for the user's yes, plus a per-user count of their messages.

    A change records the message count when it was proposed; it may only be confirmed after the user has
    sent another message, so text inside the calendar can't get a change proposed and confirmed in one turn.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def start_turn(self, user_id: int) -> int:
        """Count a new message from the user. Call once per incoming message."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO conversation_turns (user_id, turn) VALUES (?, 1) "
                "ON CONFLICT (user_id) DO UPDATE SET turn = turn + 1",
                (user_id,),
            )
        return self.current_turn(user_id)

    def current_turn(self, user_id: int) -> int:
        row = self._conn.execute("SELECT turn FROM conversation_turns WHERE user_id = ?", (user_id,)).fetchone()
        return row["turn"] if row else 0

    def create(self, user_id: int, action: str, payload: dict[str, Any]) -> PendingChange:
        with self._conn:
            self._conn.execute(
                "DELETE FROM pending_changes WHERE created_at < ?", (_utc_cutoff(PENDING_CHANGE_MAX_AGE),)
            )
            cursor = self._conn.execute(
                "INSERT INTO pending_changes (user_id, action, payload, turn) VALUES (?, ?, ?, ?)",
                (user_id, action, json.dumps(payload), self.current_turn(user_id)),
            )
        return self.get(user_id, cursor.lastrowid)

    def get(self, user_id: int, change_id: int, max_age: timedelta = PENDING_CHANGE_MAX_AGE) -> PendingChange | None:
        row = self._conn.execute(
            "SELECT * FROM pending_changes WHERE id = ? AND user_id = ?", (change_id, user_id)
        ).fetchone()
        if row is None or row["created_at"] < _utc_cutoff(max_age):
            return None
        return PendingChange(
            id=row["id"],
            action=row["action"],
            payload=json.loads(row["payload"]),
            turn=row["turn"],
            created_at=_parse_utc(row["created_at"]),
        )

    def delete(self, user_id: int, change_id: int) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM pending_changes WHERE id = ? AND user_id = ?", (change_id, user_id)
            )
        return cursor.rowcount > 0
