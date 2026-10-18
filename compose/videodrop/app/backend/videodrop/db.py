"""The one SQLite database (state_dir/videodrop.db, mode 600: callbacks'
secrets and notifications' webhook addresses are in it): the destinations,
every download (a job) and its files, the notifications and the settings.
One connection behind a lock: every query takes well under a millisecond, so
the routes and the scheduler call it directly.

The schema grows by steps (MIGRATIONS), each run once; PRAGMA user_version
says how many ran."""

import json
import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS destinations (
        slug TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        folder TEXT NOT NULL,
        template TEXT NOT NULL,
        options TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    # A download. options: what it was downloaded with, resolved (options.py);
    # progress: where it is, saved now and then (the scheduler has it live).
    """CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        site TEXT NOT NULL,
        dedupe_key TEXT NOT NULL,
        destination TEXT REFERENCES destinations (slug) ON DELETE SET NULL,
        folder TEXT,
        template TEXT NOT NULL,
        options TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 0,
        client TEXT,
        status TEXT NOT NULL,
        stage TEXT,
        progress TEXT NOT NULL DEFAULT '{}',
        title TEXT,
        uploader TEXT,
        thumbnail TEXT,
        duration REAL,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        retry_at REAL,
        callback_url TEXT,
        callback_secret TEXT,
        callback_events TEXT NOT NULL DEFAULT '[]',
        callback_last TEXT,
        base_url TEXT,
        created_at REAL NOT NULL,
        started_at REAL,
        finished_at REAL,
        updated_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, priority, created_at)",
    "CREATE INDEX IF NOT EXISTS jobs_created ON jobs (created_at)",
    "CREATE INDEX IF NOT EXISTS jobs_dedupe ON jobs (dedupe_key, status)",
    # What a download left. root: "destination" (path inside the media root)
    # or "held" (inside held_dir, removed after hold_hours).
    """CREATE TABLE IF NOT EXISTS files (
        job_id TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        root TEXT NOT NULL,
        path TEXT NOT NULL,
        name TEXT NOT NULL,
        size INTEGER NOT NULL,
        kind TEXT NOT NULL,
        existed INTEGER NOT NULL DEFAULT 0,
        removed_at REAL,
        PRIMARY KEY (job_id, position)
    )""",
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

def _checks_lyrics_and_lookups(conn: sqlite3.Connection) -> None:
    """A job's song (for its lyrics), its notes (what happened beside the
    download: lyrics found or not…) and its files' check; indexes for
    forgetting old jobs and for the client filter."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for column, definition in (("song", "TEXT"), ("notes", "TEXT NOT NULL DEFAULT '[]'")):
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
    if "checks" not in {row[1] for row in conn.execute("PRAGMA table_info(files)")}:
        conn.execute("ALTER TABLE files ADD COLUMN checks TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS jobs_finished ON jobs (status, finished_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS jobs_client ON jobs (client, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS files_held ON files (root, removed_at)")


def _fingerprints(conn: sqlite3.Connection) -> None:
    """What the site served (ytdlp.fingerprint), the earlier job whose file
    a job reused because the site serves the same video, and whether the
    job asked to download again anyway."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for column, definition in (("fingerprint", "TEXT"), ("reused_from", "TEXT"),
                               ("again", "INTEGER NOT NULL DEFAULT 0")):
        if column not in columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS jobs_fingerprint ON jobs (fingerprint, status)")


# Step n brings user_version n-1 to n. A new step goes at the end; none is ever changed.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_checks_lyrics_and_lookups, _fingerprints)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def transaction(self, work: Callable[[sqlite3.Connection], object]):
        """work(connection) all or none; what it returns."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                result = work(self._conn)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            return result

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
