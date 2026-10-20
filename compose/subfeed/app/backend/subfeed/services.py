"""The one place subfeed is put together (its composition root): the
database, the sources, the stores, the notifications and the poller, made
once and handed to the API. Nothing else makes them, so a test builds its own
with fake sources (build(sources=…))."""

import asyncio
import logging
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field

import httpx

from . import outbound
from .accounts import Accounts, open_accounts
from .config import Config
from .db import Database
from .domain import Conflict, Invalid, NotFound
from .feeds import FeedIn, FeedStore
from .notifications import TYPES, NotificationStore, Notifier
from .poller import Poller
from .posts import PostStore
from .settings import SettingsStore
from .sources import Source, registry

log = logging.getLogger("subfeed")

SEEDED = "seeded"
# The notification the environment makes: feeds failing, and working again (to #alerts).
APPRISE_NAME = "Feed problems"
TIDY_EVERY = 6 * 3600


@dataclass
class Services:
    config: Config
    db: Database
    http: httpx.AsyncClient
    sources: Mapping[str, Source]
    settings: SettingsStore
    feeds: FeedStore
    posts: PostStore
    notifications: NotificationStore
    notifier: Notifier
    poller: Poller
    accounts: Accounts
    started_at: float = field(default_factory=time.time)
    _housekeeping: asyncio.Task | None = None

    async def start(self) -> None:
        self.seed()
        self.accounts.announce(log)
        self.poller.start()
        self._housekeeping = asyncio.get_running_loop().create_task(self._tidy(), name="housekeeping")

    async def stop(self) -> None:
        if self._housekeeping:
            self._housekeeping.cancel()
            with suppress(asyncio.CancelledError):
                await self._housekeeping
        await self.poller.stop()
        await self.notifier.drain()
        await self.http.aclose()
        self.db.close()

    async def _tidy(self) -> None:
        while True:
            await asyncio.sleep(TIDY_EVERY)
            try:
                self.db.tidy()
            except Exception:
                log.exception("tidying the database failed")

    def seed(self) -> None:
        """On the first start only: the feeds and the notification the
        environment names. After that, the API owns them."""
        # The notification the first start made was called Apprise, which says how, not what: named for what it tells.
        self.db.run("UPDATE notifications SET name = ? WHERE name = 'Apprise' AND type = 'apprise'", (APPRISE_NAME,))
        if self.db.get(SEEDED):
            return
        for seed in self.config.seed:
            if self._has_feed(seed.slug):
                # Kept from before (clipfeed's database): nothing to make.
                continue
            try:
                self.feeds.create(FeedIn(slug=seed.slug, source=seed.source, target=seed.target),
                                  self.settings().default_poll_minutes)
                log.info("made feed %s (%s %s)", seed.slug, seed.source, seed.target)
            except (Conflict, Invalid) as exc:
                log.warning("no feed %s from the environment: %s", seed.slug, exc)
        if self.config.apprise_url:
            try:
                self.notifications.create({"name": APPRISE_NAME, "type": "apprise",
                                           "config": {"url": self.config.apprise_url,
                                                      "tag": self.config.apprise_tag or None}})
            except Invalid as exc:
                log.warning("no Apprise notification from the environment: %s", exc)
        self.db.put(SEEDED, True)

    def _has_feed(self, slug: str) -> bool:
        try:
            self.feeds.row(slug)
        except NotFound:
            return False
        return True


def build(config: Config, sources: Mapping[str, Source] | None = None,
          http: httpx.AsyncClient | None = None) -> Services:
    db = Database(config.database)
    http = http or outbound.client()
    sources = sources if sources is not None else registry(http, config.user_agent)
    settings = SettingsStore(db, config)
    posts = PostStore(db)
    notifications = NotificationStore(db, TYPES)
    notifier = Notifier(notifications, http)
    feeds = FeedStore(db, sources)
    poller = Poller(feeds, posts, sources, settings, notifier.emit)
    feeds.on_change(poller.wake)
    return Services(config=config, db=db, http=http, sources=sources, settings=settings, feeds=feeds, posts=posts,
                    notifications=notifications, notifier=notifier, poller=poller, accounts=open_accounts(db))
