from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras

from .settings import DEFAULTS


psycopg2.extras.register_uuid()


class Database:
    def __init__(self, url: str, migrations: Path | None = None):
        self.url = url
        self.migrations = migrations or Path(__file__).parent.parent / "migrations"
        self._local = threading.local()

    def connect(self):
        connection = psycopg2.connect(self.url, connect_timeout=5)
        connection.autocommit = True
        return connection

    def connection(self):
        connection = getattr(self._local, "connection", None)
        if connection is None or connection.closed:
            connection = self.connect()
            self._local.connection = connection
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connection()
        previous = connection.autocommit
        connection.autocommit = False
        try:
            with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                yield cursor
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.autocommit = previous

    def one(self, sql: str, params=()):
        with self.connection().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def all(self, sql: str, params=()):
        with self.connection().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def execute(self, sql: str, params=()) -> int:
        with self.connection().cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.rowcount

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
