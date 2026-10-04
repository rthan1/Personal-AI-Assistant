import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken

PREFERENCE_COLUMNS = ("name", "timezone", "home_address", "travel_mode", "buffer_minutes")
SIGNUP_TOKEN_MAX_AGE = timedelta(hours=1)


def _parse_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _utc_cutoff(max_age: timedelta) -> str:
    return (datetime.now(timezone.utc) - max_age).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class User:
    id: int
    phone: str
    name: str | None
    timezone: str
    home_address: str | None
    travel_mode: str
    buffer_minutes: int
    google_connected: bool


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        phone=row["phone"],
        name=row["name"],
        timezone=row["timezone"],
        home_address=row["home_address"],
        travel_mode=row["travel_mode"],
        buffer_minutes=row["buffer_minutes"],
        google_connected=row["google_token"] is not None,
    )


class UserRepo:
    def __init__(self, conn: sqlite3.Connection, secret_key: str):
        self._conn = conn
        self._fernet = Fernet(secret_key.encode())

    def get_by_phone(self, phone: str) -> User | None:
        row = self._conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return _row_to_user(row) if row else None

    def get(self, user_id: int) -> User | None:
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None

    def upsert(self, phone: str, timezone: str, **prefs) -> User:
        existing = self.get_by_phone(phone)
        with self._conn:
            if existing is None:
                self._conn.execute("INSERT INTO users (phone, timezone) VALUES (?, ?)", (phone, timezone))
            prefs["timezone"] = timezone
        user = self.get_by_phone(phone)
        return self.update_preferences(user.id, **prefs)

    def update_preferences(self, user_id: int, **prefs) -> User:
        unknown = set(prefs) - set(PREFERENCE_COLUMNS)
        if unknown:
            raise ValueError(f"Unknown preference(s): {', '.join(sorted(unknown))}")
        if prefs:
            assignments = ", ".join(f"{col} = ?" for col in prefs)
            with self._conn:
                self._conn.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*prefs.values(), user_id))
        return self.get(user_id)

    def get_google_token(self, user_id: int) -> str | None:
        row = self._conn.execute("SELECT google_token FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or row["google_token"] is None:
            return None
        try:
            return self._fernet.decrypt(row["google_token"].encode()).decode()
        except InvalidToken:
            return None

    def set_google_token(self, user_id: int, token_json: str | None) -> None:
        encrypted = self._fernet.encrypt(token_json.encode()).decode() if token_json else None
        with self._conn:
            self._conn.execute("UPDATE users SET google_token = ? WHERE id = ?", (encrypted, user_id))

    def create_oauth_state(self, user_id: int) -> str:
        state = secrets.token_urlsafe(32)
        with self._conn:
            self._conn.execute("INSERT INTO oauth_states (state, user_id) VALUES (?, ?)", (state, user_id))
        return state

    def consume_oauth_state(self, state: str, max_age: timedelta = timedelta(minutes=15)) -> int | None:
        with self._conn:
            row = self._conn.execute("SELECT user_id, created_at FROM oauth_states WHERE state = ?", (state,)).fetchone()
            self._conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        if row is None:
            return None
        if datetime.now(timezone.utc) - _parse_utc(row["created_at"]) > max_age:
            return None
        return row["user_id"]

    def create_signup_token(self, phone: str) -> str:
        """Token that lets whoever holds it set up the account for `phone`. Only issue it to that phone."""
        token = secrets.token_urlsafe(32)
        with self._conn:
            self._conn.execute("DELETE FROM signup_tokens WHERE created_at < ?", (_utc_cutoff(SIGNUP_TOKEN_MAX_AGE),))
            self._conn.execute("INSERT INTO signup_tokens (token, phone) VALUES (?, ?)", (token, phone))
        return token

    def phone_for_signup_token(self, token: str, max_age: timedelta = SIGNUP_TOKEN_MAX_AGE) -> str | None:
        row = self._conn.execute("SELECT phone, created_at FROM signup_tokens WHERE token = ?", (token,)).fetchone()
        if row is None or datetime.now(timezone.utc) - _parse_utc(row["created_at"]) > max_age:
            return None
        return row["phone"]

    def delete_signup_tokens(self, phone: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM signup_tokens WHERE phone = ?", (phone,))
