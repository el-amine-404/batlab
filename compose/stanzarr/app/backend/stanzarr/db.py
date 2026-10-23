"""SQLite storage (state_dir/stanzarr.db, mode 600: notifications' addresses
and the people's passwords are in it). One connection shared by the app's
threads, behind a lock.

The schema grows by steps (SCHEMA), each run once; PRAGMA user_version says
how many ran. Step 2 makes way for batlab's accounts (batlab_common/accounts.py),
which keep their own users and sessions tables: Stanzarr's one account is made
again from the environment (STANZARR_USERNAME, STANZARR_PASSWORD) at start."""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

SCHEMA = [
    # version 1
    """
    CREATE TABLE songs (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        root TEXT NOT NULL,
        rel TEXT NOT NULL,
        size INTEGER NOT NULL,
        mtime INTEGER NOT NULL,
        artist TEXT NOT NULL DEFAULT '',
        album TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        duration REAL NOT NULL DEFAULT 0,
        added_at INTEGER NOT NULL
    );
    CREATE TABLE lyrics (
        id INTEGER PRIMARY KEY,
        song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        path TEXT NOT NULL UNIQUE,
        format TEXT NOT NULL,
        synced INTEGER NOT NULL,
        language TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'unknown',
        ai_state TEXT,
        lines INTEGER NOT NULL DEFAULT 0,
        unsure INTEGER NOT NULL DEFAULT 0,
        checked INTEGER NOT NULL DEFAULT 0,
        mtime INTEGER NOT NULL
    );
    CREATE INDEX lyrics_song ON lyrics(song_id);
    CREATE TABLE attempts (
        song_id INTEGER PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
        at INTEGER NOT NULL,
        result TEXT NOT NULL
    );
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY,
        song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        stage TEXT NOT NULL DEFAULT '',
        worker TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER
    );
    CREATE INDEX jobs_status ON jobs(status);
    CREATE TABLE workers (
        name TEXT PRIMARY KEY,
        gpu TEXT NOT NULL DEFAULT '',
        vram INTEGER NOT NULL DEFAULT 0,
        version TEXT NOT NULL DEFAULT '',
        last_seen INTEGER NOT NULL
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        at INTEGER NOT NULL,
        kind TEXT NOT NULL,
        song_id INTEGER,
        message TEXT NOT NULL
    );
    CREATE INDEX events_at ON events(at);
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at INTEGER NOT NULL
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # version 2: batlab's accounts, the lyrics sources, the notifications
    """
    DROP TABLE sessions;
    DROP TABLE users;
    CREATE TABLE sources (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        position INTEGER NOT NULL,
        config TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    CREATE TABLE notifications (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        position INTEGER NOT NULL,
        events TEXT NOT NULL DEFAULT '[]',
        config TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    );
    ALTER TABLE attempts ADD COLUMN source TEXT NOT NULL DEFAULT 'lrclib';
    -- The download contract's word (docs/services.md), as songdrop's and videodrop's jobs.
    UPDATE jobs SET status='finished' WHERE status='done';
    -- GET /api/v1/lyrics for a song outside the library: the sources' answer, kept a while.
    CREATE TABLE lookups (
        key TEXT PRIMARY KEY,
        at REAL NOT NULL,
        kind TEXT NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        reference TEXT
    );
    """,
    # version 3: who did what (the activity), what identifies a song, one key per worker, the coverage by day
    """
    ALTER TABLE events ADD COLUMN actor TEXT NOT NULL DEFAULT '';
    ALTER TABLE events ADD COLUMN ok INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE events ADD COLUMN detail TEXT NOT NULL DEFAULT '';
    CREATE INDEX events_kind ON events(kind, id);
    ALTER TABLE songs ADD COLUMN albumartist TEXT NOT NULL DEFAULT '';
    ALTER TABLE songs ADD COLUMN isrc TEXT NOT NULL DEFAULT '';
    ALTER TABLE songs ADD COLUMN mbid TEXT NOT NULL DEFAULT '';
    -- Read every song's tags again once, for the new columns.
    UPDATE songs SET mtime = 0;
    CREATE TABLE worker_keys (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        key_hash TEXT NOT NULL UNIQUE,
        prefix TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_used REAL
    );
    CREATE TABLE coverage_daily (
        day TEXT PRIMARY KEY,
        counts TEXT NOT NULL
    );
    """,
    # version 4: who made the files stanzarr writes, which plain text has nowhere to say
    """
    CREATE TABLE written (
        path TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        at REAL NOT NULL
    );
    -- Every lyrics file read again once: who made those that don't say, from the searches.
    UPDATE lyrics SET mtime = 0;
    UPDATE songs SET mtime = 0;
    """,
]


class Database:
    def __init__(self, path: str):
        if not os.path.exists(path):
            # Made 600 before SQLite writes anything into it.
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self._migrate()

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(SCHEMA[version:], start=version + 1):
            self.conn.executescript("BEGIN;" + script + f"PRAGMA user_version={number}; COMMIT;")

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def all(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def run(self, sql: str, args=()) -> int:
        """The rows it changed (as batlab_common's accounts expect)."""
        with self.lock:
            return self.conn.execute(sql, args).rowcount

    def insert(self, sql: str, args=()) -> int:
        """The new row's id."""
        with self.lock:
            return self.conn.execute(sql, args).lastrowid or 0

    def get(self, key: str, default=None):
        """A setting kept as JSON (settings.py)."""
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return row["value"]

    def put(self, key: str, value) -> None:
        self.set_setting(key, json.dumps(value))

    def size(self) -> int:
        """Bytes on disk, the write-ahead log included."""
        return sum(os.path.getsize(p) for p in (self.path, self.path + "-wal") if os.path.exists(p))

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def setting(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.run("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))

    def event(self, kind: str, message: str, song_id: int | None = None, actor: str = "", ok: bool = True,
              detail: str = "") -> None:
        """The activity (Activity, Log): what happened, to which song, who asked
        (a person's or a key's name; empty: stanzarr itself), whether it went well."""
        self.run("INSERT INTO events(at, kind, song_id, message, actor, ok, detail) VALUES(?, ?, ?, ?, ?, ?, ?)",
                 (int(time.time()), kind, song_id, message, actor[:80], int(ok), detail[:2000]))
        # A bounded log: the newest 5000 events.
        self.run("DELETE FROM events WHERE id <= (SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET 5000)")
