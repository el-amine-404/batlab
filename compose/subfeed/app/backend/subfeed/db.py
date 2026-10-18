"""The one SQLite database (state_dir/subfeed.db, mode 600: notifications'
webhook addresses are in it): feeds, every post each one found, the
notifications and the settings. One connection behind a lock: every query
takes well under a millisecond, so the routes call it directly.

The schema grows by steps (MIGRATIONS), each run once; PRAGMA user_version
says how many ran. Step 1 also takes clipfeed's database, subfeed's earlier
name, as it is: its posts are kept, its YouTube Music verdicts dropped
(clipseerr owns those now)."""

import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

LEGACY_FILE = "clipfeed.db"

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
        next_at REAL NOT NULL DEFAULT 0,
        failures INTEGER NOT NULL DEFAULT 0,
        alerted INTEGER NOT NULL DEFAULT 0,
        changed_at REAL NOT NULL DEFAULT 0
    )""",
    # Every post a feed found. videos: [{"provider", "id"}]; first_video is
    # the first one's "<provider>:<id>", for "one post a video".
    """CREATE TABLE IF NOT EXISTS posts (
        feed TEXT NOT NULL REFERENCES feeds (slug) ON DELETE CASCADE,
        post_id TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        author TEXT,
        posted_at REAL NOT NULL,
        link TEXT,
        kind TEXT NOT NULL,
        domain TEXT,
        videos TEXT NOT NULL DEFAULT '[]',
        first_video TEXT,
        found_at REAL NOT NULL,
        PRIMARY KEY (feed, post_id)
    )""",
    "CREATE INDEX IF NOT EXISTS posts_feed_posted ON posts (feed, posted_at)",
    "CREATE INDEX IF NOT EXISTS posts_first_video ON posts (feed, first_video, posted_at)",
    "CREATE INDEX IF NOT EXISTS posts_posted ON posts (posted_at)",
    """CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        position INTEGER NOT NULL,
        events TEXT NOT NULL DEFAULT '[]',
        config TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    )""",
    "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)
# Columns a database made by an earlier version lacks: (table, column, definition).
COLUMNS = (
    ("feeds", "failures", "INTEGER NOT NULL DEFAULT 0"),
    ("feeds", "alerted", "INTEGER NOT NULL DEFAULT 0"),
    ("feeds", "changed_at", "REAL NOT NULL DEFAULT 0"),
)


def _adopt_clipfeed(conn: sqlite3.Connection) -> None:
    """clipfeed's entries become posts: a post carrying a YouTube video was of
    kind "youtube", now "video"; its verdicts go."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'entries'").fetchone():
        return
    conn.execute(
        """INSERT OR IGNORE INTO posts (feed, post_id, title, url, author, posted_at, link, kind, domain, videos,
                                        first_video, found_at)
           SELECT feed, post_id, title, url, author, posted_at, link,
                  CASE kind WHEN 'youtube' THEN 'video' ELSE kind END, domain,
                  (SELECT json_group_array(json_object('provider', 'youtube', 'id', value))
                   FROM json_each(entries.youtube_ids)),
                  CASE WHEN youtube_id IS NULL THEN NULL ELSE 'youtube:' || youtube_id END, found_at
           FROM entries""")
    conn.execute("DROP TABLE entries")
    conn.execute("DROP TABLE IF EXISTS videos")
    conn.execute("UPDATE feeds SET changed_at = ?", (time.time(),))


# Step n brings user_version n-1 to n. A new step goes at the end; none is ever changed.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_adopt_clipfeed,)


def adopt_legacy_file(path: Path) -> None:
    """clipfeed's database file, beside where subfeed's should be, becomes
    subfeed's (with its write-ahead log, which holds recent writes)."""
    legacy = path.with_name(LEGACY_FILE)
    if path.exists() or not legacy.exists():
        return
    for suffix in ("-wal", "-shm"):
        old = legacy.with_name(legacy.name + suffix)
        if old.exists():
            old.rename(path.with_name(path.name + suffix))
    legacy.rename(path)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        adopt_legacy_file(path)
        if not path.exists():
            # Made 600 before SQLite writes anything into it.
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "busy_timeout=5000", "foreign_keys=ON",
                       "temp_store=MEMORY"):
            self._conn.execute(f"PRAGMA {pragma}")
        with self._lock:
            self._migrate()

    def _migrate(self) -> None:
        for statement in SCHEMA:
            self._conn.execute(statement)
        for table, column, definition in COLUMNS:
            if column not in {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        done = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for number, step in enumerate(MIGRATIONS[done:], start=done + 1):
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                step(self._conn)
                self._conn.execute(f"PRAGMA user_version = {number}")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def run(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def many(self, sql: str, rows: list[tuple]) -> int:
        """One statement for each row, all or none; the rows it changed."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                changed = sum(self._conn.execute(sql, row).rowcount for row in rows)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            return changed

    # ---- a small key-value store, for the settings --------------------------

    def get(self, key: str, default=None):
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value) -> None:
        self.run("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value)))

    def size(self) -> int:
        """Bytes on disk, the write-ahead log included."""
        return sum(p.stat().st_size for p in (self.path, self.path.with_name(self.path.name + "-wal"))
                   if p.exists())

    def tidy(self) -> None:
        """Now and then: the query planner's statistics brought up to date, the
        write-ahead log folded back into the database."""
        with self._lock:
            self._conn.execute("PRAGMA optimize")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
