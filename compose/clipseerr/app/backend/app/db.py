"""The one SQLite database (state_dir/clipseerr.db, mode 600: services' API
keys are in it). One connection behind a lock: every query here takes well
under a millisecond, so the routes call it directly."""

import json
import os
import sqlite3
import threading
from pathlib import Path

def _indexed(table: str, search: str, columns: tuple[str, ...]) -> list[str]:
    """An FTS5 index of table's columns (accents and case ignored, prefixes of
    2 and 3 letters kept for typing), and the triggers that keep it current."""
    names = ", ".join(columns)
    new = ", ".join(f"new.{c}" for c in columns)
    old = ", ".join(f"old.{c}" for c in columns)
    drop = f"INSERT INTO {search} ({search}, rowid, {names}) VALUES ('delete', old.rowid, {old});"
    add = f"INSERT INTO {search} (rowid, {names}) VALUES (new.rowid, {new});"
    return [
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {search} USING fts5({names}, content='{table}', "
        "content_rowid='rowid', tokenize='unicode61 remove_diacritics 2', prefix='2 3')",
        f"CREATE TRIGGER IF NOT EXISTS {search}_add AFTER INSERT ON {table} BEGIN {add} END",
        f"CREATE TRIGGER IF NOT EXISTS {search}_drop AFTER DELETE ON {table} BEGIN {drop} END",
        f"CREATE TRIGGER IF NOT EXISTS {search}_change AFTER UPDATE ON {table} BEGIN {drop} {add} END",
    ]


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
    "CREATE INDEX IF NOT EXISTS cache_fetched ON cache (fetched_at)",
    # Every video and artist shown (known.py), and their full-text indexes (kept by the triggers).
    """CREATE TABLE IF NOT EXISTS known_videos (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL, video TEXT NOT NULL,
        sign TEXT NOT NULL, seen_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS known_artists (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, thumbnail TEXT, subscribers TEXT,
        sign TEXT NOT NULL, seen_at REAL NOT NULL
    )""",
    # The language a video's lyrics are in (catalog/language.py), once read.
    "CREATE TABLE IF NOT EXISTS known_languages (video_id TEXT PRIMARY KEY, language TEXT NOT NULL)",
    *(statement
      for table, search, columns in (("known_videos", "known_search", ("title", "artist")),
                                     ("known_artists", "known_artist_search", ("name",)))
      for statement in _indexed(table, search, columns)),
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
        # Sorting and the full-text index's work in memory; 32 MB of pages kept, and read through mmap.
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA cache_size=-32000")
        self._conn.execute("PRAGMA mmap_size=134217728")
        with self._lock:
            for statement in SCHEMA:
                self._conn.execute(statement)
            # Who asked for each request (accounts.py): "user:<id>" or "key:<id>", and their name then.
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(requests)")}
            for column in ("owner", "owner_name"):
                if column not in columns:
                    self._conn.execute(f"ALTER TABLE requests ADD COLUMN {column} TEXT")
            self._conn.execute("CREATE INDEX IF NOT EXISTS requests_owner ON requests (owner, created_at)")

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

    def tidy(self) -> None:
        """Now and then: the query planner's statistics brought up to date, the
        write-ahead log folded back into the database."""
        with self._lock:
            self._conn.execute("PRAGMA optimize")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
