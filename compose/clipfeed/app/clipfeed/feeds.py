"""The feeds and what they found (db.py), and the poller that fills them.

Each feed is read every poll_minutes: Reddit's newest posts, else PullPush's
when Reddit refuses; a new feed first takes its history from PullPush
(BACKFILL_PAGES of 100 posts). Every video a post links to is then checked
once with YouTube Music (verify.py)."""

import asyncio
import logging
import re
import time
from contextlib import suppress

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import verify
from .db import Database
from .sources import Post, Reddit, Refused

log = logging.getLogger("clipfeed")

BACKFILL_PAGES = 5
# Videos checked at once, and per round (the rest wait for the next round).
CHECKS_AT_ONCE = 3
CHECKS_PER_ROUND = 300
# Verdicts change: a song reaches YouTube Music, a video is taken down.
RECHECK_DAYS = {"official": 30, "other": 7}
TICK = 30
MAX_BACKOFF = 6 * 3600


class NotFound(LookupError):
    """The message is shown as is."""


class Conflict(ValueError):
    """The message is shown as is."""


class FeedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str | None = Field(None, pattern=r"^[a-z0-9-]{1,40}$",
                             description="Its name in URLs; made from the title when left out. Fixed once made.")
    title: str = Field(min_length=1, max_length=80)
    source: str = Field("reddit", pattern="^reddit$", description="Where its posts come from; reddit only, today.")
    target: str = Field(pattern=r"^[A-Za-z0-9_]{2,21}$", description="The subreddit, without r/.",
                        examples=["SexyMusicVideos"])
    enabled: bool = True
    poll_minutes: int = Field(30, ge=15, le=1440, description="How often it is read.")

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, value):
        return re.sub(r"^/?r/", "", str(value or "").strip()).strip("/")


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:40] or "feed"


def _feed(row) -> dict:
    return {
        "slug": row["slug"], "title": row["title"], "source": row["source"], "target": row["target"],
        "enabled": bool(row["enabled"]), "poll_minutes": row["poll_minutes"], "created_at": row["created_at"],
        "link": f"https://www.reddit.com/r/{row['target']}/",
        "last": {"at": row["last_at"], "ok": bool(row["last_ok"]), "message": row["last_message"]}
        if row["last_at"] else None,
        "next_at": row["next_at"] or None,
    }


ITEM_SQL = """SELECT p.*, v.verdict, v.kind, v.title AS video_title, v.artist, v.duration, v.published, v.song_id,
    v.checked_at FROM posts p LEFT JOIN videos v ON v.youtube_id = p.youtube_id"""


def _item(row) -> dict:
    return {
        "id": f"{row['feed']}:{row['youtube_id']}",
        "feed": row["feed"],
        "youtube_id": row["youtube_id"],
        "url": f"https://www.youtube.com/watch?v={row['youtube_id']}",
        "verdict": row["verdict"] or "unchecked",
        "kind": row["kind"],
        "title": row["video_title"],
        "artist": row["artist"],
        "duration": row["duration"],
        "published": row["published"],
        "song_id": row["song_id"],
        "checked_at": row["checked_at"],
        "post": {"id": row["post_id"], "title": row["title"], "url": row["url"], "author": row["author"],
                 "posted_at": row["posted_at"]},
    }


class Feeds:
    def __init__(self, db: Database, reddit: Reddit):
        self._db = db
        self._reddit = reddit
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---- the feeds -----------------------------------------------------------------

    def list(self) -> list[dict]:
        return [self._counted(_feed(r)) for r in self._db.all("SELECT * FROM feeds ORDER BY created_at")]

    def get(self, slug: str) -> dict:
        row = self._db.one("SELECT * FROM feeds WHERE slug = ?", (slug,))
        if not row:
            raise NotFound(f"No feed {slug!r}.")
        return self._counted(_feed(row))

    def _counted(self, feed: dict) -> dict:
        row = self._db.one("SELECT COUNT(*) AS n, SUM(v.verdict = 'official') AS official, "
                           "SUM(v.verdict = 'likely') AS likely FROM posts p "
                           "LEFT JOIN videos v ON v.youtube_id = p.youtube_id WHERE p.feed = ?", (feed["slug"],))
        return {**feed, "items": row["n"], "official": row["official"] or 0, "likely": row["likely"] or 0}

    def create(self, body: FeedIn) -> dict:
        slug = body.slug or _slugify(body.title)
        if self._db.one("SELECT 1 FROM feeds WHERE slug = ?", (slug,)):
            raise Conflict(f"There is a feed {slug!r} already.")
        self._db.run("INSERT INTO feeds (slug, title, source, target, enabled, poll_minutes, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (slug, body.title, body.source, body.target, int(body.enabled), body.poll_minutes, time.time()))
        self._wake.set()
        return self.get(slug)

    def update(self, slug: str, body: FeedIn) -> dict:
        old = self.get(slug)
        if body.slug and body.slug != slug:
            raise Conflict("A feed's slug can't change: make a new feed.")
        moved = body.target.casefold() != old["target"].casefold()
        self._db.run("UPDATE feeds SET title = ?, target = ?, enabled = ?, poll_minutes = ?, next_at = 0"
                     + (", backfilled = 0" if moved else "") + " WHERE slug = ?",
                     (body.title, body.target, int(body.enabled), body.poll_minutes, slug))
        if moved:
            # Another subreddit: what the old one found isn't this feed's.
            self._db.run("DELETE FROM posts WHERE feed = ?", (slug,))
        self._wake.set()
        return self.get(slug)

    def delete(self, slug: str) -> None:
        self.get(slug)
        self._db.run("DELETE FROM posts WHERE feed = ?", (slug,))
        self._db.run("DELETE FROM feeds WHERE slug = ?", (slug,))

    def refresh(self, slug: str) -> dict:
        self.get(slug)
        self._db.run("UPDATE feeds SET next_at = 0 WHERE slug = ?", (slug,))
        self._wake.set()
        return self.get(slug)

    # ---- what they found --------------------------------------------------------------

    def items(self, slugs: list[str], verdicts: list[str], sort: str, limit: int, offset: int) -> tuple[list[dict], int]:
        for slug in slugs:
            self.get(slug)
        where, params = [], []
        if slugs:
            where.append(f"p.feed IN ({','.join('?' * len(slugs))})")
            params += slugs
        if verdicts:
            where.append(f"COALESCE(v.verdict, 'unchecked') IN ({','.join('?' * len(verdicts))})")
            params += verdicts
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        order = ("COALESCE(v.published, date(p.posted_at, 'unixepoch')) DESC, p.posted_at DESC"
                 if sort == "published" else "p.posted_at DESC")
        total = self._db.one(f"SELECT COUNT(*) AS n FROM posts p LEFT JOIN videos v ON v.youtube_id = p.youtube_id"
                             f"{clause}", tuple(params))["n"]
        rows = self._db.all(f"{ITEM_SQL}{clause} ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit, offset))
        return [_item(r) for r in rows], total

    def _add(self, slug: str, posts: list[Post]) -> int:
        added, now = 0, time.time()
        for post in posts:
            for youtube_id in post.videos:
                added += self._db.run(
                    "INSERT INTO posts (feed, youtube_id, post_id, title, url, author, posted_at, found_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (feed, youtube_id) DO UPDATE SET "
                    # The first post that linked it is the one kept.
                    "post_id = excluded.post_id, title = excluded.title, url = excluded.url, "
                    "author = excluded.author, posted_at = excluded.posted_at "
                    "WHERE excluded.posted_at < posts.posted_at",
                    (slug, youtube_id, post.id, post.title[:300], post.url, post.author, post.posted_at, now))
        return added

    # ---- the poller --------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run(), name="poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            try:
                for row in self._db.all("SELECT * FROM feeds WHERE enabled = 1 AND next_at <= ? ORDER BY next_at",
                                        (time.time(),)):
                    await self.poll(row)
                await self.check_videos()
            except Exception:
                log.exception("a poll round failed")
            self._wake.clear()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), TICK)

    async def poll(self, row) -> None:
        slug, target, every = row["slug"], row["target"], row["poll_minutes"] * 60
        notes, ok, retry = [], True, None
        try:
            if not row["backfilled"]:
                count = await self._backfill(slug, target)
                notes.append(f"{count} videos from PullPush's history")
            try:
                posts = await self._reddit.newest(target)
                notes.append(f"{self._add(slug, posts)} new of Reddit's last {len(posts)} posts")
            except Refused as exc:
                retry = exc.retry_after
                posts = await self._reddit.archive(target)
                notes.append(f"Reddit refused ({exc}); {self._add(slug, posts)} new from PullPush")
        except Exception as exc:
            ok = False
            notes.append(f"failed: {exc}"[:300])
            log.warning("feed %s: %s", slug, exc)
        # Failing again: wait longer each time, up to MAX_BACKOFF.
        wait = every if ok else min(MAX_BACKOFF, max(retry or 0, every * (4 if row["last_ok"] == 0 else 2)))
        self._db.run("UPDATE feeds SET last_at = ?, last_ok = ?, last_message = ?, next_at = ? WHERE slug = ?",
                     (time.time(), int(ok), "; ".join(notes), time.time() + wait, slug))
        log.info("feed %s: %s", slug, "; ".join(notes))

    async def _backfill(self, slug: str, target: str) -> int:
        added, before = 0, None
        for _ in range(BACKFILL_PAGES):
            posts = await self._reddit.archive(target, before)
            added += self._add(slug, posts)
            if len(posts) < 100:
                break
            before = min(p.posted_at for p in posts)
        self._db.run("UPDATE feeds SET backfilled = 1 WHERE slug = ?", (slug,))
        return added

    async def check_videos(self) -> int:
        """Checks the videos not checked yet (newest posts first), then the
        ones checked under older rules or long ago."""
        now = time.time()
        rows = self._db.all(
            "SELECT p.youtube_id FROM posts p LEFT JOIN videos v ON v.youtube_id = p.youtube_id "
            "WHERE v.youtube_id IS NULL OR v.rules < ? OR v.checked_at < CASE v.verdict WHEN 'official' "
            "THEN ? ELSE ? END GROUP BY p.youtube_id ORDER BY v.youtube_id IS NOT NULL, MAX(p.posted_at) DESC LIMIT ?",
            (verify.RULES, now - RECHECK_DAYS["official"] * 86400, now - RECHECK_DAYS["other"] * 86400,
             CHECKS_PER_ROUND))
        gate = asyncio.Semaphore(CHECKS_AT_ONCE)

        async def one(youtube_id: str) -> bool:
            async with gate:
                try:
                    found = await verify.check_async(youtube_id)
                except Exception as exc:
                    # YouTube didn't answer: the next round asks again.
                    log.info("checking %s failed: %r", youtube_id, exc)
                    return False
            fields = found.row()
            self._db.run("INSERT OR REPLACE INTO videos (youtube_id, verdict, kind, title, artist, duration, "
                         "published, song_id, checked_at, rules) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (youtube_id, fields["verdict"], fields["kind"], fields["title"], fields["artist"],
                          fields["duration"], fields["published"], fields["song_id"], time.time(), verify.RULES))
            return True

        done = await asyncio.gather(*(one(r["youtube_id"]) for r in rows))
        return sum(done)
