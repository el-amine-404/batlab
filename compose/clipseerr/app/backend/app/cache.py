"""What the catalog's sources answered, kept so that the page never waits on
them twice: in memory, and in the database so a restart starts warm.

A value older than its fresh time is still answered at once, and fetched
again in the background (stale-while-revalidate); only a value older than its
stale time, or none, is waited for. Two callers asking for the same key share
one fetch.
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Awaitable, Callable
from typing import Any

from .db import Database

log = logging.getLogger("clipseerr.cache")

MEMORY_ENTRIES = 2000


# When what a request is answering with was fetched: tracking() collects it.
_reads: ContextVar[list[float] | None] = ContextVar("cache_reads", default=None)
# Only values kept fresh this long or less say how old a list is: a video's
# release day, kept a year, would make every list look a year old.
LIST_FRESHNESS = 6 * 3600


def _note(fetched_at: float, fresh: float) -> None:
    reads = _reads.get()
    if reads is not None and fresh <= LIST_FRESHNESS:
        reads.append(fetched_at)


@contextmanager
def tracking():
    """with tracking() as reads: … : reads holds when each cached value the
    block used was fetched, its own and its tasks' (they share the list); the
    oldest is how old the answer is."""
    reads: list[float] = []
    token = _reads.set(reads)
    try:
        yield reads
    finally:
        _reads.reset(token)


class Cache:
    def __init__(self, db: Database):
        self._db = db
        self._memory: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future] = {}
        self._background: set[asyncio.Task] = set()
        self.hits = 0
        self.misses = 0

    # ---- reading ---------------------------------------------------------------

    def _read(self, key: str) -> tuple[float, Any] | None:
        found = self._memory.get(key)
        if found:
            self._memory.move_to_end(key)
            return found
        row = self._db.one("SELECT value, fetched_at FROM cache WHERE key = ?", (key,))
        if not row:
            return None
        found = (row["fetched_at"], json.loads(row["value"]))
        self._remember(key, found)
        return found

    def _remember(self, key: str, entry: tuple[float, Any]) -> None:
        self._memory[key] = entry
        self._memory.move_to_end(key)
        while len(self._memory) > MEMORY_ENTRIES:
            self._memory.popitem(last=False)

    def _write(self, key: str, value: Any) -> None:
        now = time.time()
        self._remember(key, (now, value))
        self._db.run("INSERT INTO cache (key, value, fetched_at) VALUES (?, ?, ?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value, fetched_at = excluded.fetched_at",
                     (key, json.dumps(value, ensure_ascii=False), now))

    def put(self, key: str, value: Any) -> None:
        """Stores value as just fetched: for what one call answered besides
        what it was asked for."""
        self._write(key, value)

    def peek(self, key: str) -> Any:
        """What the cache holds for key, however old, without fetching: None when nothing."""
        found = self._read(key)
        return found[1] if found else None

    # ---- the one call ------------------------------------------------------------

    async def get(self, key: str, fetch: Callable[[], Awaitable[Any]], fresh: float, stale: float | None = None) -> Any:
        """The value of key: fetch()'s answer, at most fresh seconds old, or
        up to stale seconds old (default 7 days) while a new one is fetched."""
        stale = stale if stale is not None else 7 * 86400
        found = self._read(key)
        if found:
            age = time.time() - found[0]
            if age < fresh or age < stale:
                self.hits += 1
                if age >= fresh:
                    self._refresh(key, fetch)
                _note(found[0], fresh)
                return found[1]
        self.misses += 1
        value = await self._fetch(key, fetch)
        _note(time.time(), fresh)
        return value

    async def _fetch(self, key: str, fetch: Callable[[], Awaitable[Any]]) -> Any:
        running = self._inflight.get(key)
        if running:
            return await asyncio.shield(running)
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await fetch()
        except BaseException as exc:
            future.set_exception(exc)
            # Nobody else may be waiting: don't let the future log "never retrieved".
            future.exception()
            raise
        else:
            self._write(key, value)
            future.set_result(value)
            return value
        finally:
            self._inflight.pop(key, None)

    def _refresh(self, key: str, fetch: Callable[[], Awaitable[Any]]) -> None:
        if key in self._inflight:
            return

        async def run():
            try:
                await self._fetch(key, fetch)
            except Exception as exc:  # the stale value stays
                log.warning("refreshing %s failed: %s", key, exc)

        task = asyncio.get_running_loop().create_task(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def forget(self, prefix: str = "") -> int:
        """Drops every entry whose key starts with prefix (all with "")."""
        for key in [key for key in self._memory if key.startswith(prefix)]:
            del self._memory[key]
        return self._db.run("DELETE FROM cache WHERE key LIKE ? ESCAPE '\\'",
                            (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",))

    def prune(self, older_than: float) -> int:
        return self._db.run("DELETE FROM cache WHERE fetched_at < ?", (time.time() - older_than,))

    def stats(self) -> dict:
        row = self._db.one("SELECT COUNT(*) AS n FROM cache")
        return {"entries": row["n"] if row else 0, "in_memory": len(self._memory), "hits": self.hits,
                "misses": self.misses}
