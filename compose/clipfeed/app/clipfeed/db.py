"""The one SQLite database (state_dir/clipfeed.db): the feeds, every post each
one found, and what YouTube said about each video. One connection behind a
lock: every query takes well under a millisecond."""

import sqlite3
import threading
from pathlib import Path

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS feeds (
        slug TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        poll_minutes INTEGER NOT NULL,
        created_at REAL NOT NULL,
        backfilled INTEGER NOT NULL DEFAULT 0,
        last_at REAL,
        last_ok INTEGER,
        last_message TEXT,
        next_at REAL NOT NULL DEFAULT 0
    )""",
    # Every post a feed found; youtube_id is its (first) video, when it has one.
    """CREATE TABLE IF NOT EXISTS entries (
        feed TEXT NOT NULL REFERENCES feeds (slug) ON DELETE CASCADE,
        post_id TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        author TEXT,
        posted_at REAL NOT NULL,
        link TEXT,
        kind TEXT NOT NULL,
        domain TEXT,
        youtube_id TEXT,
        youtube_ids TEXT NOT NULL DEFAULT '[]',
        found_at REAL NOT NULL,
        PRIMARY KEY (feed, post_id)
    )""",
    "CREATE INDEX IF NOT EXISTS entries_posted ON entries (feed, posted_at)",
    "CREATE INDEX IF NOT EXISTS entries_video ON entries (youtube_id)",
    # What YouTube Music says about a video, whichever feed linked it.
    """CREATE TABLE IF NOT EXISTS videos (
        youtube_id TEXT PRIMARY KEY,
        verdict TEXT NOT NULL,
        kind TEXT,
        title TEXT,
        artist TEXT,
        duration INTEGER,
        published TEXT,
        song_id TEXT,
        checked_at REAL NOT NULL,
        rules INTEGER NOT NULL DEFAULT 0
    )""",
)
# Columns added after a first release: (table, column, definition).
COLUMNS = (("videos", "rules", "INTEGER NOT NULL DEFAULT 0"),)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "busy_timeout=5000", "foreign_keys=ON"):
            self._conn.execute(f"PRAGMA {pragma}")
        with self._lock:
            for statement in SCHEMA:
                self._conn.execute(statement)
            # Before entries, one row a video (posts): read again into entries.
            if self._conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'posts'").fetchone():
                self._conn.execute("DROP TABLE posts")
                self._conn.execute("UPDATE feeds SET backfilled = 0, next_at = 0")
            for table, column, definition in COLUMNS:
                if column not in {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def run(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
