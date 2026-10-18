"""Reads every feed when it is due, and keeps what it finds.

A feed is read every poll_minutes: its source's newest posts, else a page of
its history when the source refuses (Reddit's limits; PullPush stands in). A
new feed first takes backfill_posts of its history. A read that fails waits
longer before the next one (doubling, up to MAX_BACKOFF, never sooner than
the source asked), and after failing_after failures in a row the feed is
said to be failing (feed.failing), once, until a read works again
(feed.recovered). Posts older than keep_days are forgotten.

It never stops: one feed failing, or a round, is logged and the next goes
on."""

import asyncio
import logging
import random
import time
from collections.abc import Callable, Mapping
from contextlib import suppress

from .events import Event
from .feeds import FeedStore
from .posts import PostStore
from .settings import Settings
from .sources import Refused, Source, SourceError

log = logging.getLogger("subfeed.poller")

TICK = 30
MAX_BACKOFF = 6 * 3600
FORGET_EVERY = 3600
# What a posts.new event lists at most.
NEW_POSTS_SHOWN = 10


class Poller:
    def __init__(self, feeds: FeedStore, posts: PostStore, sources: Mapping[str, Source],
                 settings: Callable[[], Settings], emit: Callable[[Event], None]):
        self._feeds = feeds
        self._posts = posts
        self._sources = sources
        self._settings = settings
        self._emit = emit
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._forgot_at = 0.0
        self.last_round: float | None = None

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run(), name="poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.round()
            except Exception:
                log.exception("a round failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), TICK)

    async def round(self) -> None:
        for row in self._feeds.due(time.time()):
            await self.poll(row)
        self._forget()
        self.last_round = time.time()

    # ---- one feed -----------------------------------------------------------------

    async def poll(self, row) -> None:
        slug, settings = row["slug"], self._settings()
        source = self._sources.get(row["source"])
        notes: list[str] = []
        ok, retry, added, started = True, None, 0, time.time()
        try:
            if source is None:
                raise SourceError(f"subfeed has no source {row['source']!r} any more")
            if not row["backfilled"]:
                count = await self._backfill(slug, row["target"], source, settings.backfill_posts)
                notes.append(f"{count} posts from {source.archive_label}'s history")
            try:
                posts = await source.newest(row["target"])
                added = self._posts.add(slug, posts)
                notes.append(f"{added} new of {source.label}'s last {len(posts)} posts")
            except Refused as exc:
                retry = exc.retry_after
                posts = await source.history(row["target"])
                added = self._posts.add(slug, posts)
                notes.append(f"{source.label} refused ({exc}); {added} new from {source.archive_label}")
        except SourceError as exc:
            ok = False
            notes.append(f"Failed: {exc}.")
            log.warning("feed %s: %s", slug, exc)
        except Exception as exc:
            ok = False
            notes.append(f"Failed: {exc.__class__.__name__}.")
            log.exception("feed %s", slug)
        if added:
            self._feeds.touched(slug)
        failures = 0 if ok else row["failures"] + 1
        alerted = self._tell(row, ok, failures, settings, "; ".join(notes))
        self._feeds.record(slug, ok, "; ".join(notes), started + self._wait(row["poll_minutes"] * 60, failures,
                                                                            retry), failures, alerted)
        if added and row["backfilled"]:
            self._tell_new(row, started, settings)
        log.info("feed %s: %s", slug, "; ".join(notes))

    @staticmethod
    def _wait(every: float, failures: int, retry_after: float | None) -> float:
        """Seconds to the next read: every, or longer after failures, never
        sooner than the source asked; give or take a tenth, so that feeds
        spread out."""
        wait = every if not failures else min(MAX_BACKOFF, every * 2 ** min(failures, 8))
        return max(wait, retry_after or 0) * random.uniform(0.9, 1.1)

    async def _backfill(self, slug: str, target: str, source: Source, posts: int) -> int:
        added, before = 0, None
        for _ in range(-(-posts // source.page_size)):
            page = await source.history(target, before)
            added += self._posts.add(slug, page)
            if len(page) < source.page_size:
                break
            before = min(p.posted_at for p in page)
        self._feeds.backfilled(slug)
        return added

    def _forget(self) -> None:
        keep_days = self._settings().keep_days
        if not keep_days or time.time() - self._forgot_at < FORGET_EVERY:
            return
        self._forgot_at = time.time()
        for slug in self._posts.forget(time.time() - keep_days * 86400):
            self._feeds.touched(slug)

    # ---- events ---------------------------------------------------------------------

    def _feed_url(self, slug: str, settings: Settings) -> str | None:
        return f"{settings.public_url}/feeds/{slug}" if settings.public_url else None

    def _tell(self, row, ok: bool, failures: int, settings: Settings, message: str) -> bool:
        """feed.failing once a feed has failed failing_after times in a row,
        feed.recovered on its next good read; whether it has been told."""
        slug, title = row["slug"], row["title"]
        if ok:
            if row["alerted"]:
                self._emit(Event("feed.recovered", f"{title} works again", (message,),
                                 self._feed_url(slug, settings), {"feed": slug}))
            return False
        if row["alerted"] or failures < settings.failing_after:
            return bool(row["alerted"])
        self._emit(Event("feed.failing", f"{title} fails",
                         (f"{failures} reads in a row failed. The last: {message}",
                          "subfeed keeps trying, less often, and says when it works again."),
                         self._feed_url(slug, settings), {"feed": slug, "failures": failures}))
        return True

    def _tell_new(self, row, since: float, settings: Settings) -> None:
        found = self._posts.newest(row["slug"], since)
        if not found:
            return
        lines = tuple(f"• [{post['title'][:120]}]({post['url']})" for post in found[:NEW_POSTS_SHOWN])
        if len(found) > NEW_POSTS_SHOWN:
            lines += (f"… and {len(found) - NEW_POSTS_SHOWN} more.",)
        self._emit(Event("posts.new", f"{row['title']}: {len(found)} new post{'s' if len(found) > 1 else ''}",
                         lines, self._feed_url(row["slug"], settings), {"feed": row["slug"], "posts": found}))
