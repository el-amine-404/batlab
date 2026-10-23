"""The one place Stanzarr is put together (its composition root): the
database, the accounts, the sources, the notifications and the background
work, made once and handed to the API. A test builds its own with build()."""

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx

from . import library, operations, outbound
from .accounts import Accounts, open_accounts
from .config import Config
from .db import Database
from .domain import Conflict, Invalid, NotFound
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
AUTO_AI_BATCH = 20  # songs sent to the workers by themselves per pass, at most
APPRISE_NAME = "Lyrics news"


class Background:
    """Scan, then ask the sources, then (when on) send plain songs to the
    workers, then tidy the queue and note the day's coverage; every
    scan_minutes, or now when woken. What it is doing, and how far, is in
    busy and progress (the Activity page)."""

    def __init__(self, services: "Services"):
        self.s = services
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.busy = ""
        self.progress: dict = {}
        self.last_scan: dict = {}
        self.thread = threading.Thread(target=self.run, name="stanzarr-background", daemon=True)

    def _step(self, busy: str, **progress) -> None:
        self.busy, self.progress = busy, progress

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                self._step("scanning the library", seen=0)
                self.last_scan = library.scan(self.s.db, self.s.config.library,
                                              lambda seen, folder: self._step("scanning the library", seen=seen,
                                                                              folder=folder)) | {"at": int(time.time())}
                self.search_pass()
                self.auto_ai_pass()
                self._step("")
                self.requeue_stale()
                self.s.note_coverage()
            except Exception:  # keep going: one bad file or a network outage is not fatal
                log.exception("background pass failed")
                self._step("")
            self.wake.wait(self.s.settings().scan_minutes * 60)
            self.wake.clear()

    def search_pass(self) -> None:
        sources = self.s.sources.asked()
        if not sources:
            return
        settings = self.s.settings()
        retry = int(time.time()) - settings.retry_days * 86400
        candidates = self.s.db.all(
            f"SELECT s.id FROM songs s LEFT JOIN attempts a ON a.song_id=s.id WHERE ({library.RANK_SQL}) < ?"
            " AND (a.at IS NULL OR a.at < ?) ORDER BY a.at IS NOT NULL, a.at LIMIT ?",
            (library.LEVELS[settings.cutoff], retry, SEARCH_BATCH))
        for n, row in enumerate(candidates):
            if self.stop.is_set():
                return
            self._step("asking the sources", done=n, total=len(candidates))
            try:
                self.s.search(row["id"])
            except Exception as error:  # every source down: try again next pass
                log.warning("no source answered for song %s: %s", row["id"], error)
                return

    def auto_ai_pass(self) -> None:
        """Songs with words and no timings, which the sources were asked
        about, to the workers: when on, a GPU is online, and AI is below the cutoff."""
        settings = self.s.settings()
        if not settings.auto_ai or library.LEVELS[settings.cutoff] <= library.LEVELS["plain"]:
            return
        if not any(w["gpu"] for w in self.s.workers_online()):
            return
        rows = self.s.db.all(
            f"SELECT s.id, s.artist, s.title FROM songs s JOIN attempts a ON a.song_id=s.id WHERE ({library.RANK_SQL}) = 1"
            " AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.song_id=s.id) ORDER BY s.added_at DESC LIMIT ?",
            (AUTO_AI_BATCH,))
        for row in rows:
            self.s.db.insert("INSERT INTO jobs(song_id, kind, status, created_at) VALUES(?, 'ai_sync', 'queued', ?)",
                             (row["id"], int(time.time())))
            self.s.db.event("ai", f"{row['artist']} – {row['title']}: AI sync queued (plain words only)", row["id"])

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

    def search(self, song_id: int, actor: str = "") -> str:
        """Ask the sources for one song now (operations.search); every source
        failing to answer is in the activity too."""

        def found(song: dict, how: str) -> None:
            self.notifier.emit(Event("lyrics.found", f"{song['artist']} – {song['title']}: {how}", (),
                                     self.link(f"/songs/{song['id']}"),
                                     {"song": {"id": song["id"], "artist": song["artist"], "title": song["title"]},
                                      "synced": how.startswith("synced")}))

        try:
            return operations.search(self.db, self.config, song_id, self.sources.asked(), self.sources.note, found, actor)
        except Exception as error:
            song = operations.song_or_none(self.db, song_id) or {"artist": "?", "title": str(song_id)}
            self.db.event("search", f"{song['artist']} – {song['title']}: no source answered", song_id, actor, ok=False,
                          detail=str(error)[:500])
            raise

    def note_coverage(self) -> None:
        """Today's coverage, for its history (Library's chart): the last pass of the day wins."""
        found = {r["status"]: r["n"] for r in self.db.all(
            f"SELECT {library.STATUS_SQL} AS status, COUNT(*) AS n FROM songs s GROUP BY status")}
        counts = {name: found.get(name, 0) for name in library.STATUSES}
        self.db.run("INSERT INTO coverage_daily(day, counts) VALUES(?, ?) ON CONFLICT(day) DO UPDATE SET counts=excluded.counts",
                    (time.strftime("%Y-%m-%d"), json.dumps(counts)))

    def workers_online(self) -> list[dict]:
        return self.db.all("SELECT * FROM workers WHERE last_seen >= ? ORDER BY last_seen DESC",
                           (int(time.time()) - ONLINE_SECONDS,))

    # ---- the workers' keys: one each ------------------------------------------------

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def legacy_key(self) -> str | None:
        """The one key all workers shared before 1.1: STANZARR_WORKER_KEY, or the
        one kept in the database. It goes on working until removed."""
        return self.config.worker_key or self.db.setting("worker_key") or None

    def worker_for(self, key: str, claimed: str) -> str | None:
        """Which worker a key is: its own name, or (the shared key) the name it gives."""
        if not key:
            return None
        row = self.db.one("SELECT id, name FROM worker_keys WHERE key_hash=?", (self._hash(key),))
        if row:
            self.db.run("UPDATE worker_keys SET last_used=? WHERE id=?", (time.time(), row["id"]))
            return row["name"]
        legacy = self.legacy_key()
        if legacy and hmac.compare_digest(key, legacy):
            return (claimed or "worker")[:64]
        return None

    def worker_keys(self) -> list[dict]:
        return self.db.all("SELECT id, name, prefix, created_at, last_used FROM worker_keys ORDER BY name")

    def add_worker(self, name: str) -> tuple[str, dict]:
        """A worker with a key of its own; the key is answered this once."""
        name = name.strip()
        if not name or len(name) > 64:
            raise Invalid("A worker's name is 1 to 64 characters.")
        if self.db.one("SELECT 1 FROM worker_keys WHERE name=? COLLATE NOCASE", (name,)):
            raise Conflict(f"There's a worker called {name!r} already.")
        key = "szw_" + secrets.token_urlsafe(32)
        worker_id = uuid.uuid4().hex[:12]
        self.db.run("INSERT INTO worker_keys(id, name, key_hash, prefix, created_at) VALUES(?, ?, ?, ?, ?)",
                    (worker_id, name, self._hash(key), key[:8], time.time()))
        return key, self.db.one("SELECT id, name, prefix, created_at, last_used FROM worker_keys WHERE id=?", (worker_id,))

    def rename_worker(self, worker_id: str, name: str) -> dict:
        row = self.db.one("SELECT name FROM worker_keys WHERE id=?", (worker_id,))
        if not row:
            raise NotFound("There's no such worker.")
        name = name.strip()
        if not name or len(name) > 64:
            raise Invalid("A worker's name is 1 to 64 characters.")
        if self.db.one("SELECT 1 FROM worker_keys WHERE name=? COLLATE NOCASE AND id!=?", (name, worker_id)):
            raise Conflict(f"There's a worker called {name!r} already.")
        self.db.run("UPDATE worker_keys SET name=? WHERE id=?", (name, worker_id))
        self.db.run("UPDATE workers SET name=? WHERE name=?", (name, row["name"]))
        return self.db.one("SELECT id, name, prefix, created_at, last_used FROM worker_keys WHERE id=?", (worker_id,))

    def remove_worker(self, worker_id: str) -> None:
        """Its key stops working at once; a job it was running goes back to the queue."""
        row = self.db.one("SELECT name FROM worker_keys WHERE id=?", (worker_id,))
        if not row:
            raise NotFound("There's no such worker.")
        self.db.run("DELETE FROM worker_keys WHERE id=?", (worker_id,))
        self.db.run("DELETE FROM workers WHERE name=?", (row["name"],))
        self.db.run("UPDATE jobs SET status='queued', worker='', stage='' WHERE status='running' AND worker=?",
                    (row["name"],))

    def forget_legacy_key(self) -> None:
        if self.config.worker_key:
            raise Conflict("The shared key is set by STANZARR_WORKER_KEY: remove it from compose/.env.")
        self.db.run("DELETE FROM settings WHERE key='worker_key'")

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
