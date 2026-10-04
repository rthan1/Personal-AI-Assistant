import sqlite3
from pathlib import Path

# Each entry upgrades the schema by one version; never edit an entry once shipped, append a new one.
MIGRATIONS = [
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        phone TEXT NOT NULL UNIQUE,
        name TEXT,
        timezone TEXT NOT NULL,
        home_address TEXT,
        travel_mode TEXT NOT NULL DEFAULT 'drive',
        buffer_minutes INTEGER NOT NULL DEFAULT 10,
        google_token TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE TABLE oauth_states (
        state TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    """,
    """
    CREATE TABLE signup_tokens (
        token TEXT PRIMARY KEY,
        phone TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE INDEX signup_tokens_phone ON signup_tokens (phone);
    """,
    """
    CREATE TABLE memories (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE INDEX memories_user_id ON memories (user_id);
    """,
    """
    CREATE TABLE conversation_turns (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        turn INTEGER NOT NULL
    );
    CREATE TABLE pending_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        action TEXT NOT NULL,
        payload TEXT NOT NULL,
        turn INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE INDEX pending_changes_user_id ON pending_changes (user_id);
    """,
    """
    ALTER TABLE users ADD COLUMN default_reminder_minutes INTEGER;
    CREATE TABLE reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        event_id TEXT NOT NULL,
        event_start TEXT NOT NULL,
        minutes_before INTEGER NOT NULL,
        remind_at TEXT NOT NULL,
        is_default INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        UNIQUE (user_id, event_id, minutes_before)
    );
    CREATE INDEX reminders_due ON reminders (status, remind_at);
    """,
    """
    ALTER TABLE users ADD COLUMN briefing_time TEXT;
    ALTER TABLE users ADD COLUMN briefing_sent_on TEXT;
    """,
    """
    CREATE TABLE contacts (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE INDEX contacts_user_id ON contacts (user_id);
    CREATE TABLE invite_counts (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day TEXT NOT NULL,
        count INTEGER NOT NULL,
        PRIMARY KEY (user_id, day)
    );
    """,
]


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for index in range(version, len(MIGRATIONS)):
        conn.executescript(f"BEGIN; {MIGRATIONS[index]} PRAGMA user_version = {index + 1}; COMMIT;")
