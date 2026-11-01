from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras
import psycopg2.pool

from .settings import DEFAULTS, known

psycopg2.extras.register_uuid()

_GONE = (psycopg2.OperationalError, psycopg2.InterfaceError)


class Database:
    """A bounded pool. A connection the server dropped (a database restart) is
    thrown away, and a single statement outside a transaction tried once more,
    so a restart of PostgreSQL never leaves a request hanging or failing for good."""

    def __init__(self, url: str, migrations: Path | None = None, size: int = 12, idle: int = 4):
        self.url = url
        self.migrations = migrations or Path(__file__).parent.parent / "migrations"
        self.size = size
        # Kept open between requests: a new connection costs PostgreSQL a password check.
        self.idle = min(idle, size)
        self._pool: psycopg2.pool.ThreadedConnectionPool | None = None
        self._slots = threading.BoundedSemaphore(size)
        self._lock = threading.Lock()

    def connect(self):
        connection = psycopg2.connect(self.url, connect_timeout=5, application_name="scanloom",
                                      keepalives=1, keepalives_idle=30, keepalives_interval=10)
        connection.autocommit = True
        return connection

    def _get_pool(self):
        with self._lock:
            if self._pool is None:
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    self.idle, self.size, self.url, connect_timeout=5, application_name="scanloom")
            return self._pool

    @contextmanager
    def _connection(self):
        pool = self._get_pool()
        if not self._slots.acquire(timeout=30):
            raise TimeoutError("The database is busy")
        connection = None
        broken = False
        try:
            connection = pool.getconn()
            connection.autocommit = True
            yield connection
        except _GONE:
            broken = True
            # The others in the pool died with it (a database restart): the next asks a new pool.
            with self._lock:
                if self._pool is pool:
                    self._pool = None
            raise
        finally:
            if connection is not None:
                try:
                    pool.putconn(connection, close=broken or bool(connection.closed))
                except psycopg2.pool.PoolError:
                    connection.close()  # its pool was closed meanwhile, by a database restart
            if broken:
                try:
                    pool.closeall()
                except psycopg2.Error:
                    pass
            self._slots.release()

    @contextmanager
    def transaction(self):
        with self._connection() as connection:
            connection.autocommit = False
            try:
                with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    yield cursor
                connection.commit()
            except BaseException:
                if not connection.closed:
                    connection.rollback()
                raise
            finally:
                if not connection.closed:
                    connection.autocommit = True

    def _run(self, method, sql: str, params):
        for attempt in (1, 2):
            try:
                with self._connection() as connection:
                    with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                        cursor.execute(sql, params)
                        return method(cursor)
            except _GONE:
                if attempt == 2:
                    raise

    def one(self, sql: str, params=()):
        return self._run(lambda cursor: cursor.fetchone(), sql, params)

    def all(self, sql: str, params=()):
        return self._run(lambda cursor: cursor.fetchall(), sql, params)

    def execute(self, sql: str, params=()) -> int:
        return self._run(lambda cursor: cursor.rowcount, sql, params)

    def settings(self) -> dict:
        return known(dict(self.one("SELECT values FROM settings WHERE singleton=true")["values"]))

    def migrate(self):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(hashtext('scanloom-migrations'))")
                cursor.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
                for path in sorted(self.migrations.glob("*.sql")):
                    cursor.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.name,))
                    if cursor.fetchone():
                        continue
                    connection.autocommit = False
                    try:
                        cursor.execute(path.read_text(encoding="utf-8"))
                        cursor.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.name,))
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
                    finally:
                        connection.autocommit = True
                cursor.execute("INSERT INTO settings(singleton, values) VALUES (true, %s::jsonb) ON CONFLICT DO NOTHING", (json.dumps(DEFAULTS),))
                cursor.execute("SELECT pg_advisory_unlock(hashtext('scanloom-migrations'))")
        finally:
            connection.close()
