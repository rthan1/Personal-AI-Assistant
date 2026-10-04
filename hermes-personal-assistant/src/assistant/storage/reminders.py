import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from assistant.storage.users import _parse_utc

MAX_PENDING_PER_USER = 50
KEEP_FINISHED_FOR = timedelta(days=2)


def _utc_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Reminder:
    id: int
    user_id: int
    event_id: str
    event_start: datetime
    minutes_before: int
    remind_at: datetime
    is_default: bool
    status: str


def _row_to_reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=row["id"],
        user_id=row["user_id"],
        event_id=row["event_id"],
        event_start=_parse_utc(row["event_start"]),
        minutes_before=row["minutes_before"],
        remind_at=_parse_utc(row["remind_at"]),
        is_default=bool(row["is_default"]),
        status=row["status"],
    )


class ReminderRepo:
    """Texts to send a set time before calendar events. Status: pending, sent, skipped, or cancelled.

    Only the event id and times are stored; the title and location are re-read from Google when sending.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def add(self, user_id: int, event_id: str, event_start: datetime, minutes_before: int) -> Reminder:
        """A reminder the user asked for. Re-arms an existing one for the same event and lead time."""
        if len(self.list_pending(user_id)) >= MAX_PENDING_PER_USER:
            raise ValueError(f"You can have at most {MAX_PENDING_PER_USER} upcoming reminders. Cancel one first.")
        remind_at = event_start - timedelta(minutes=minutes_before)
        with self._conn:
            self._conn.execute(
                "INSERT INTO reminders (user_id, event_id, event_start, minutes_before, remind_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (user_id, event_id, minutes_before) DO UPDATE SET "
                "event_start = excluded.event_start, remind_at = excluded.remind_at, status = 'pending', "
                "is_default = 0",
                (user_id, event_id, _utc_text(event_start), minutes_before, _utc_text(remind_at)),
            )
        return self._get_by_event(user_id, event_id, minutes_before)

    def ensure_default(self, user_id: int, event_id: str, event_start: datetime, minutes_before: int) -> None:
        """Adds the automatic reminder for an event unless one already exists (even a cancelled one)."""
        remind_at = event_start - timedelta(minutes=minutes_before)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO reminders "
                "(user_id, event_id, event_start, minutes_before, remind_at, is_default) VALUES (?, ?, ?, ?, ?, 1)",
                (user_id, event_id, _utc_text(event_start), minutes_before, _utc_text(remind_at)),
            )

    def list_pending(self, user_id: int) -> list[Reminder]:
        rows = self._conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND status = 'pending' ORDER BY remind_at, id", (user_id,)
        ).fetchall()
        return [_row_to_reminder(row) for row in rows]

    def cancel(self, user_id: int, reminder_id: int) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND user_id = ? AND status = 'pending'",
                (reminder_id, user_id),
            )
        return cursor.rowcount > 0

    def cancel_all(self, user_id: int) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE reminders SET status = 'cancelled' WHERE user_id = ? AND status = 'pending'", (user_id,)
            )
        return cursor.rowcount

    def delete_pending_defaults(self, user_id: int) -> None:
        """Called when the default lead time changes; the scheduler recreates them with the new one."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM reminders WHERE user_id = ? AND is_default = 1 AND status = 'pending'", (user_id,)
            )

    def due(self, now: datetime) -> list[Reminder]:
        """Pending reminders for every user whose time has come. For the scheduler only."""
        rows = self._conn.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND remind_at <= ? ORDER BY remind_at, id",
            (_utc_text(now),),
        ).fetchall()
        return [_row_to_reminder(row) for row in rows]

    def reschedule(self, reminder_id: int, event_start: datetime) -> Reminder:
        """The event moved: keep the same lead time before its new start."""
        row = self._conn.execute("SELECT minutes_before FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        remind_at = event_start - timedelta(minutes=row["minutes_before"])
        with self._conn:
            self._conn.execute(
                "UPDATE reminders SET event_start = ?, remind_at = ? WHERE id = ?",
                (_utc_text(event_start), _utc_text(remind_at), reminder_id),
            )
        return self._get(reminder_id)

    def mark(self, reminder_id: int, status: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))

    def prune(self, now: datetime) -> None:
        """Drops finished reminders for events that are long past."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM reminders WHERE status != 'pending' AND event_start < ?",
                (_utc_text(now - KEEP_FINISHED_FOR),),
            )

    def _get(self, reminder_id: int) -> Reminder:
        return _row_to_reminder(self._conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone())

    def _get_by_event(self, user_id: int, event_id: str, minutes_before: int) -> Reminder:
        row = self._conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND event_id = ? AND minutes_before = ?",
            (user_id, event_id, minutes_before),
        ).fetchone()
        return _row_to_reminder(row)
