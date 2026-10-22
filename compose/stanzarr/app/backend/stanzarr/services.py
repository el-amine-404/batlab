"""The one place Stanzarr is put together (its composition root): the
database, the accounts, the sources, the notifications and the background
work, made once and handed to the API. A test builds its own with build()."""

import asyncio
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

import httpx

from . import library, operations, outbound
from .accounts import Accounts, open_accounts
from .config import Config
from .db import Database
from .domain import Invalid
from .events import Event
from .notifications import TYPES as NOTIFICATION_TYPES
from .notifications import NotificationStore, Notifier
from .settings import SettingsStore
from .sources import SourceStore

log = logging.getLogger("stanzarr")

SEEDED = "seeded"
ONLINE_SECONDS = 60  # a worker that checked in within this is online
STALE_SECONDS = 300  # a running job whose worker went quiet this long is queued again
SEARCH_BATCH = 200  # questions to the sources per pass, so a big first scan stays polite
APPRISE_NAME = "Lyrics news"


class Background:
    """Scan, then ask the sources, then tidy the queue; every scan_minutes,
    or now when woken."""

    def __init__(self, services: "Services"):
        self.s = services
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.busy = ""
        self.last_scan: dict = {}
        self.thread = threading.Thread(target=self.run, name="stanzarr-background", daemon=True)

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self.busy = "scanning"
                self.last_scan = library.scan(self.s.db, self.s.config.library) | {"at": int(time.time())}
                self.busy = "asking the sources"
                self.search_pass()
                self.busy = ""
                self.requeue_stale()
            except Exception:  # keep going: one bad file or a network outage is not fatal
                log.exception("background pass failed")
                self.busy = ""
            self.wake.wait(self.s.settings().scan_minutes * 60)
            self.wake.clear()

    def search_pass(self) -> None:
        sources = self.s.sources.asked()
        if not sources:
            return
        retry = int(time.time()) - self.s.settings().retry_days * 86400
        candidates = self.s.db.all(
            "SELECT s.id FROM songs s LEFT JOIN attempts a ON a.song_id=s.id"
            " WHERE NOT EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.synced=1 AND l.source!='ai')"
            " AND (a.at IS NULL OR a.at < ?) ORDER BY a.at IS NOT NULL, a.at LIMIT ?", (retry, SEARCH_BATCH))
        for row in candidates:
            if self.stop.is_set():
                return
            try:
                self.s.search(row["id"])
            except Exception as error:  # every source down: try again next pass
                log.warning("no source answered for song %s: %s", row["id"], error)
                return

    def requeue_stale(self) -> None:
        cutoff = int(time.time()) - STALE_SECONDS
        self.s.db.run("UPDATE jobs SET status='queued', worker='', stage='' WHERE status='running' AND worker NOT IN"
                      " (SELECT name FROM workers WHERE last_seen >= ?)", (cutoff,))


@dataclass
class Services:
    config: Config
    db: Database
    http: httpx.AsyncClient
    accounts: Accounts
    settings: SettingsStore
    sources: SourceStore
    notifications: NotificationStore
    notifier: Notifier
    started_at: float = field(default_factory=time.time)
    background: Background | None = None

    async def start(self, run_background: bool = True) -> None:
        self.seed()
        self.accounts.announce(log)
        self.notifier.start(asyncio.get_running_loop())
        self.background = Background(self)
        if run_background:
            self.background.thread.start()

    async def stop(self) -> None:
        if self.background:
            self.background.stop.set()
            self.background.wake.set()
        await self.notifier.drain()
        await self.http.aclose()

    # ---- what the routes and the background share ----------------------------------

    def link(self, path: str) -> str | None:
        base = self.settings().public_url
        return f"{base}{path}" if base else None

    def search(self, song_id: int) -> str:
        """Ask the sources for one song now (operations.search)."""

        def found(song: dict, how: str) -> None:
            self.notifier.emit(Event("lyrics.found", f"{song['artist']} – {song['title']}: {how}", (),
                                     self.link(f"/songs/{song['id']}"),
                                     {"song": {"id": song["id"], "artist": song["artist"], "title": song["title"]},
                                      "synced": how.startswith("synced")}))

        return operations.search(self.db, self.config, song_id, self.sources.asked(), self.sources.note, found)

    def workers_online(self) -> list[dict]:
        return self.db.all("SELECT * FROM workers WHERE last_seen >= ? ORDER BY last_seen DESC",
                           (int(time.time()) - ONLINE_SECONDS,))

    def worker_key(self) -> str:
        if self.config.worker_key:
            return self.config.worker_key
        key = self.db.setting("worker_key")
        if not key:
            key = secrets.token_urlsafe(24)
            self.db.set_setting("worker_key", key)
        return key

    def new_worker_key(self) -> str:
        key = secrets.token_urlsafe(24)
        self.db.set_setting("worker_key", key)
        return key

    # ---- the first start ------------------------------------------------------------

    def seed(self) -> None:
        """The first admin from the environment while nobody exists; on the
        first start only, LRCLIB as the first source (unless it was turned
        off before 1.0) and the notification the environment names."""
        if self.accounts.needs_setup() and self.config.username and self.config.password:
            try:
                self.accounts.create_user(self.config.username, self.config.username, self.config.password, ["admin"])
                log.info("made the admin %s from the environment", self.config.username)
            except Exception as exc:  # a password too short for the accounts: the setup code then
                log.warning("no admin from the environment (%s): use the setup code", exc)
        if self.db.get(SEEDED):
            return
        if self.config.lrclib and self.db.setting("lrclib", "1") != "0":
            try:
                self.sources.create({"name": "LRCLIB", "type": "lrclib"})
            except Invalid as exc:
                log.warning("no LRCLIB source: %s", exc)
        if self.config.apprise_url:
            try:
                self.notifications.create({"name": APPRISE_NAME, "type": "apprise",
                                           "config": {"url": self.config.apprise_url,
                                                      "tag": self.config.apprise_tag or None}})
            except Invalid as exc:
                log.warning("no Apprise notification from the environment: %s", exc)
        self.db.put(SEEDED, True)


def build(config: Config | None = None, http: httpx.AsyncClient | None = None) -> Services:
    config = config or Config()
    config.ensure_dirs()
    db = Database(config.db_path)
    http = http or outbound.client()
    notifications = NotificationStore(db, NOTIFICATION_TYPES)
    return Services(config=config, db=db, http=http, accounts=open_accounts(db), settings=SettingsStore(db, config),
                    sources=SourceStore(db), notifications=notifications, notifier=Notifier(notifications, http))
