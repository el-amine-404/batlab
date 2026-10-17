"""The one SQLite database (state_dir/clipseerr.db, mode 600: services' API
keys are in it). One connection behind a lock: every query here takes well
under a millisecond, so the routes call it directly."""

import json
import os
import sqlite3
import threading
from pathlib import Path

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        video_key TEXT NOT NULL,
        payload TEXT NOT NULL,
        attempts TEXT NOT NULL DEFAULT '[]',
        message TEXT,
        quality TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        finished_at REAL
    )""",
    "CREATE INDEX IF NOT EXISTS requests_status ON requests (status)",
    "CREATE INDEX IF NOT EXISTS requests_video ON requests (video_key, kind)",
    "CREATE INDEX IF NOT EXISTS requests_created ON requests (created_at)",
    # Services (download apps) and notifications: one row each, by family.
    """CREATE TABLE IF NOT EXISTS integrations (
        id TEXT PRIMARY KEY,
        family TEXT NOT NULL,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        position INTEGER NOT NULL,
        events TEXT NOT NULL DEFAULT '[]',
        config TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    )""",
    "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, fetched_at REAL NOT NULL)",
)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists()
        if fresh:
            # Made 600 before SQLite writes anything into it.
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            for statement in SCHEMA:
                self._conn.execute(statement)

    def all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def run(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def many(self, statements: list[tuple[str, tuple]]) -> None:
        """Several writes, all or none."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for sql, params in statements:
                    self._conn.execute(sql, params)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    # ---- a small key-value store, for the settings -------------------------

    def get(self, key: str, default=None):
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value) -> None:
        self.run("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (key, json.dumps(value)))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
