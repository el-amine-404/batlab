"""SQLite storage. One connection shared by the app's threads, writes under a lock."""

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
]


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA synchronous=NORMAL")
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
        with self.lock:
            return self.conn.execute(sql, args).lastrowid or 0

    def setting(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.run("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))

    def event(self, kind: str, message: str, song_id: int | None = None) -> None:
        self.run("INSERT INTO events(at, kind, song_id, message) VALUES(?, ?, ?, ?)",
                 (int(time.time()), kind, song_id, message))
        # A bounded log: the newest 5000 events.
        self.run("DELETE FROM events WHERE id <= (SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET 5000)")
