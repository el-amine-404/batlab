"""The feeds: which community each one reads, how often, and how its last
read went. A feed's posts are its own (posts.py): they go with it, and go
when it is pointed at another community."""

import re
import sqlite3
import time
from collections.abc import Callable, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import Database
from .domain import Conflict, Invalid, NotFound
from .settings import POLL_MINUTES
from .sources import Source

# Every feed is read on one address's allowance (sources/fetch.py).
MAX_FEEDS = 50
_SLUG = r"^[a-z0-9-]{1,40}$"


class FeedIn(BaseModel):
    """A new feed. Only the community is needed."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=200,
                        description="The community: a subreddit's name, r/Name, or its address.",
                        examples=["SexyMusicVideos"])
    source: str = Field("reddit", description="Where its posts come from (GET /api/v1/sources).")
    title: str | None = Field(None, max_length=80, description="Its name; the community's (r/Name) when left out.")
    slug: str | None = Field(None, pattern=_SLUG,
                             description="Its name in addresses; made from the community's when left out. Fixed "
                                         "once made.")
    enabled: bool = Field(True, description="Off: kept, but not read.")
    poll_minutes: int | None = Field(None, ge=POLL_MINUTES[0], le=POLL_MINUTES[1],
                                     description="How often it is read; the settings' default when left out.")

    @field_validator("title")
    @classmethod
    def _title(cls, value):
        return (value or "").strip() or None


class FeedPatch(BaseModel):
    """Only the fields to change."""

    model_config = ConfigDict(extra="forbid")

    target: str | None = Field(None, min_length=1, max_length=200,
                               description="Another community: the posts found so far go.")
    title: str | None = Field(None, min_length=1, max_length=80)
    enabled: bool | None = None
    poll_minutes: int | None = Field(None, ge=POLL_MINUTES[0], le=POLL_MINUTES[1])


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:40] or "feed"


class FeedStore:
    def __init__(self, db: Database, sources: Mapping[str, Source]):
        self._db = db
        self._sources = sources
        self._listeners: list[Callable[[], None]] = []

    def on_change(self, listener: Callable[[], None]) -> None:
        """listener() runs when a feed is made, changed or asked to be read now
        (the poller, which reads it then)."""
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in self._listeners:
            listener()

    # ---- what the API offers -------------------------------------------------------

    def public(self, row: sqlite3.Row, counts: Mapping[str, dict] | None = None) -> dict:
        source = self._sources.get(row["source"])
        counted = (counts or {}).get(row["slug"], {})
        return {
            "slug": row["slug"],
            "title": row["title"],
            "source": row["source"],
            "target": row["target"],
            "home_url": source.home(row["target"]) if source else None,
            "enabled": bool(row["enabled"]),
            "poll_minutes": row["poll_minutes"],
            "created_at": row["created_at"],
            "last": {"at": row["last_at"], "ok": bool(row["last_ok"]), "message": row["last_message"]}
            if row["last_at"] else None,
            "next_at": row["next_at"] or None,
            "failures": row["failures"],
            "changed_at": row["changed_at"],
            "posts": counted.get("posts", 0),
            "videos": counted.get("videos", 0),
        }

    def row(self, slug: str) -> sqlite3.Row:
        found = self._db.one("SELECT * FROM feeds WHERE slug = ?", (slug,))
        if not found:
            raise NotFound(f"There's no feed {slug!r}.")
        return found

    def rows(self) -> list[sqlite3.Row]:
        return self._db.all("SELECT * FROM feeds ORDER BY created_at")

    def create(self, body: FeedIn, default_poll_minutes: int) -> sqlite3.Row:
        source = self._source(body.source)
        target = source.normalize(body.target)
        slug = body.slug or _slugify(target)
        if self._db.one("SELECT 1 FROM feeds WHERE slug = ?", (slug,)):
            raise Conflict(f"There's a feed {slug!r} already: give this one another slug.")
        if self._db.one("SELECT COUNT(*) AS n FROM feeds")["n"] >= MAX_FEEDS:
            raise Conflict(f"subfeed reads at most {MAX_FEEDS} feeds.")
        now = time.time()
        self._db.run("INSERT INTO feeds (slug, title, source, target, enabled, poll_minutes, created_at, changed_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (slug, body.title or source.title(target), source.key, target, int(body.enabled),
                      body.poll_minutes or default_poll_minutes, now, now))
        self._changed()
        return self.row(slug)

    def update(self, slug: str, patch: FeedPatch) -> sqlite3.Row:
        old = self.row(slug)
        changes = patch.model_dump(exclude_none=True)
        if "target" in changes:
            changes["target"] = self._source(old["source"]).normalize(changes["target"])
        moved = "target" in changes and changes["target"].casefold() != old["target"].casefold()
        if "enabled" in changes:
            changes["enabled"] = int(changes["enabled"])
        if moved:
            # Another community: what the old one posted isn't this feed's.
            changes.update(backfilled=0, failures=0, alerted=0, last_at=None, last_ok=None, last_message=None)
            self._db.run("DELETE FROM posts WHERE feed = ?", (slug,))
        changes.update(next_at=0, changed_at=time.time())
        assignments = ", ".join(f"{column} = ?" for column in changes)
        self._db.run(f"UPDATE feeds SET {assignments} WHERE slug = ?", (*changes.values(), slug))
        self._changed()
        return self.row(slug)

    def delete(self, slug: str) -> None:
        self.row(slug)
        # Its posts go with it (ON DELETE CASCADE).
        self._db.run("DELETE FROM feeds WHERE slug = ?", (slug,))

    def refresh(self, slug: str) -> sqlite3.Row:
        self.row(slug)
        self._db.run("UPDATE feeds SET next_at = 0 WHERE slug = ?", (slug,))
        self._changed()
        return self.row(slug)

    def version(self, slugs: list[str] | None = None) -> str:
        """Changes whenever what these feeds answer could: for ETags."""
        where, params = ("", ()) if not slugs else (f" WHERE slug IN ({','.join('?' * len(slugs))})", tuple(slugs))
        row = self._db.one(f"SELECT COUNT(*) AS n, MAX(changed_at) AS at FROM feeds{where}", params)
        return f"{row['n']}-{row['at'] or 0}"

    # ---- what the poller needs -------------------------------------------------------

    def due(self, now: float) -> list[sqlite3.Row]:
        return self._db.all("SELECT * FROM feeds WHERE enabled = 1 AND next_at <= ? ORDER BY next_at", (now,))

    def backfilled(self, slug: str) -> None:
        self._db.run("UPDATE feeds SET backfilled = 1 WHERE slug = ?", (slug,))

    def touched(self, slug: str) -> None:
        """Its posts changed."""
        self._db.run("UPDATE feeds SET changed_at = ? WHERE slug = ?", (time.time(), slug))

    def record(self, slug: str, ok: bool, message: str, next_at: float, failures: int, alerted: bool) -> None:
        self._db.run("UPDATE feeds SET last_at = ?, last_ok = ?, last_message = ?, next_at = ?, failures = ?, "
                     "alerted = ? WHERE slug = ?",
                     (time.time(), int(ok), message[:500], next_at, failures, int(alerted), slug))

    def _source(self, key: str) -> Source:
        source = self._sources.get(key)
        if source is None:
            raise Invalid(f"subfeed has no source {key!r}; it has {', '.join(self._sources)}.")
        return source
