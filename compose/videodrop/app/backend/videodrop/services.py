"""The one place videodrop is put together (its composition root): the
database, the stores, the notifications, the callbacks, the live stream and
the scheduler, made once and handed to the API. Nothing else makes them, so
a test builds its own (build(config, python=…) with a fake runner)."""

import asyncio
import json
import logging
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass, field

import httpx

from . import outbound, placement
from .broker import Broker
from .callbacks import Callbacks
from .config import Config
from .db import Database
from .destinations import DestinationIn, DestinationStore
from .domain import Conflict, Invalid, NotFound
from .jobs import JobIn, JobStore
from .notifications import TYPES, NotificationStore, Notifier
from .options import DEFAULT_TEMPLATE, resolve
from .scheduler import Scheduler
from .settings import SettingsStore

log = logging.getLogger("videodrop")

SEEDED = "seeded"
HOUSEKEEPING_EVERY = 600
TIDY_EVERY = 6 * 3600


@dataclass
class Services:
    config: Config
    db: Database
    http: httpx.AsyncClient
    settings: SettingsStore
    destinations: DestinationStore
    jobs: JobStore
    notifications: NotificationStore
    notifier: Notifier
    broker: Broker
    callbacks: Callbacks
    scheduler: Scheduler
    started_at: float = field(default_factory=time.time)
    _housekeeping: asyncio.Task | None = None

    async def start(self) -> None:
        self.broker.bind(asyncio.get_running_loop())
        self.seed()
        self.scheduler.start()
        self._housekeeping = asyncio.get_running_loop().create_task(self._keep_house(), name="housekeeping")

    async def stop(self) -> None:
        if self._housekeeping:
            self._housekeeping.cancel()
            with suppress(asyncio.CancelledError):
                await self._housekeeping
        await self.scheduler.stop()
        await self.callbacks.drain()
        await self.notifier.drain()
        await self.http.aclose()
        self.db.close()

    # ---- asking for a download ---------------------------------------------------------------------

    def submit(self, body: JobIn, base_url: str | None) -> tuple[sqlite3.Row, bool]:
        """The job (new, or the same one already queued or running) and
        whether it is new. Its destination, options and file name are fixed
        now: a destination changed later changes the downloads after."""
        settings = self.settings()
        slug = body.destination if body.destination is not None else settings.default_destination
        destination = None
        if slug:
            try:
                destination = self.destinations.row(slug)
            except NotFound:
                raise Invalid(f"There's no destination {slug!r} (GET /api/v1/destinations).") from None
        options = resolve(body.options.model_dump(exclude_none=True),
                          json.loads(destination["options"]) if destination else None, base=settings.defaults)
        template = destination["template"] if destination else DEFAULT_TEMPLATE
        return self.jobs.create(body, destination=destination, options=options, template=template,
                                base_url=base_url)

    # ---- housekeeping ---------------------------------------------------------------------------------

    async def _keep_house(self) -> None:
        tidied = time.time()
        while True:
            try:
                await asyncio.to_thread(self.keep_house)
                if time.time() - tidied >= TIDY_EVERY:
                    tidied = time.time()
                    self.db.tidy()
            except Exception:
                log.exception("housekeeping failed")
            await asyncio.sleep(HOUSEKEEPING_EVERY)

    def keep_house(self, now: float | None = None) -> None:
        """Held files past hold_hours go; ended jobs past keep_days are forgotten."""
        now = now or time.time()
        settings = self.settings()
        forget_before = now - settings.keep_days * 86400 if settings.keep_days else None
        held_before = now - settings.hold_hours * 3600
        # A job's held files are all in held_dir/<job id>, and expire together.
        for file in self.jobs.expired_held(max(held_before, forget_before or 0)):
            placement.clear(self.config.held_dir / file["job_id"])
            self.jobs.file_removed(file["job_id"], file["position"])
        if forget_before:
            forgotten = self.jobs.forget(forget_before)
            if forgotten:
                log.info("forgot %d downloads older than %d days", forgotten, settings.keep_days)

    def remove_job(self, job_id: str) -> None:
        """Forgets an ended job, and its held files; files in a destination stay."""
        self.jobs.delete(job_id)
        placement.clear(self.config.held_dir / job_id)

    # ---- the first start ------------------------------------------------------------------------------

    def seed(self) -> None:
        """On the first start only: the destinations and the notification the
        environment names. After that, the API owns them."""
        if self.db.get(SEEDED):
            return
        for seed in self.config.seed:
            if self.destinations.exists(seed.slug):
                continue
            try:
                self.destinations.create(DestinationIn(name=seed.slug.replace("-", " ").capitalize(),
                                                       slug=seed.slug, folder=seed.folder))
                log.info("made destination %s (%s)", seed.slug, seed.folder)
            except (Conflict, Invalid) as exc:
                log.warning("no destination %s from the environment: %s", seed.slug, exc)
        if self.config.apprise_url:
            try:
                self.notifications.create({"name": "Apprise", "type": "apprise",
                                           "config": {"url": self.config.apprise_url,
                                                      "tag": self.config.apprise_tag or None}})
            except Invalid as exc:
                log.warning("no Apprise notification from the environment: %s", exc)
        self.db.put(SEEDED, True)


def build(config: Config, http: httpx.AsyncClient | None = None) -> Services:
    db = Database(config.database)
    http = http or outbound.client()
    settings = SettingsStore(db, config)
    destinations = DestinationStore(db, config.media_root)
    jobs = JobStore(db, config.held_dir, config.media_root)
    notifications = NotificationStore(db, TYPES)
    notifier = Notifier(notifications, http)
    broker = Broker()
    callbacks = Callbacks(http, lambda job_id, last: _record_callback(jobs, job_id, last))

    scheduler = Scheduler(config, jobs, settings, notifier.emit, callbacks, broker.publish)
    jobs.live_from(scheduler.live)
    jobs.on_change(broker.publish)
    jobs.on_change(scheduler.wake)
    destinations.on_remove(settings.forget_destination)
    return Services(config=config, db=db, http=http, settings=settings, destinations=destinations, jobs=jobs,
                    notifications=notifications, notifier=notifier, broker=broker, callbacks=callbacks,
                    scheduler=scheduler)


def _record_callback(jobs: JobStore, job_id: str, last: dict) -> None:
    with suppress(NotFound):
        jobs.row(job_id)
        jobs.update(job_id, callback_last=last)
