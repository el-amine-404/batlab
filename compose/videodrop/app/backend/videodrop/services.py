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
from batlab_common.egress import Proxy

from . import links, outbound, placement
from .accounts import Accounts, Principal, open_accounts
from .broker import Broker
from .callbacks import Callbacks
from .config import Config
from .db import Database
from .destinations import DestinationIn, DestinationStore
from .domain import Conflict, Forbidden, Invalid, NotFound
from .issues import Issues, ReleaseWatch
from .jobs import JobIn, JobStore, section_text
from .journal import Journal
from .notifications import TYPES, NotificationStore, Notifier
from .options import DEFAULT_TEMPLATE, OptionsPatch, Section, VideoOptions, resolve, to_output_template
from .previews import Previews
from .prober import Prober
from .scheduler import Scheduler
from .sites import Sites
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
    prober: Prober
    releases: ReleaseWatch
    issues: Issues
    previews: Previews
    journal: Journal
    sites: Sites
    egress: Proxy
    accounts: Accounts
    started_at: float = field(default_factory=time.time)
    _tasks: list[asyncio.Task] = field(default_factory=list)

    async def start(self) -> None:
        self.broker.bind(asyncio.get_running_loop())
        self.seed()
        self.accounts.announce(log)
        self.scheduler.start()
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._keep_house(), name="housekeeping")]
        if self.config.check_releases:
            self._tasks.append(loop.create_task(self.releases.run(), name="releases"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self.scheduler.stop()
        await self.callbacks.drain()
        await self.notifier.drain()
        self.previews.close()
        self.egress.stop()
        await self.http.aclose()
        self.db.close()

    # ---- asking for a download ---------------------------------------------------------------------

    def plan(self, destination: str | None, options: OptionsPatch) -> tuple[sqlite3.Row | None, VideoOptions, str]:
        """Where a download would go, with what options and under which file
        name: the destination asked for (None: the settings' default; "":
        none), the options of the job, then the destination's, then the
        settings'."""
        settings = self.settings()
        slug = destination if destination is not None else settings.default_destination
        row = None
        if slug:
            try:
                row = self.destinations.row(slug)
            except NotFound:
                raise Invalid(f"There's no destination {slug!r} (GET /api/v1/destinations).") from None
        resolved = resolve(options.model_dump(exclude_none=True), json.loads(row["options"]) if row else None,
                           base=settings.defaults)
        return row, resolved, row["template"] if row else DEFAULT_TEMPLATE

    @staticmethod
    def allowed_destination(who: Principal | None, destination: str | None) -> str | None:
        """The destination who may ask for: without that permission, none
        (the files are kept here), and asking for one is refused."""
        if who is None or who.can("destinations"):
            return destination
        if destination:
            raise Forbidden(f"{who.name} may only keep downloads here, not save them into a destination: an admin "
                            "gives that permission.")
        return ""

    def submit(self, body: JobIn, base_url: str | None, who: Principal | None = None) -> tuple[sqlite3.Row, bool]:
        """The job (new, or the same one of who's already queued or running)
        and whether it is new. Its destination, options and file name are
        fixed now: a destination changed later changes the downloads after.
        A callback is an app's: only an API key may ask for one (a person on
        the page would otherwise make videodrop call any address here)."""
        if who is not None and who.kind != "key" and body.callback_url:
            raise Forbidden("Callbacks are for apps: ask with an API key (Settings → API keys).")
        body = body.model_copy(update={"destination": self.allowed_destination(who, body.destination)})
        destination, options, template = self.plan(body.destination, body.options)
        row, new = self.jobs.create(body, destination=destination, options=options, template=template,
                                    base_url=base_url, owner=who.owner if who else None,
                                    owner_name=who.name if who else None)
        if new:
            where = f"to {row['destination']} ({row['folder']})" if row["folder"] else \
                f"kept here {self.jobs.hold_hours(row)} h for its caller"
            part = f", only {Section(**dict(zip(('start', 'end'), json.loads(row['section'])))).label()}" \
                if row["section"] else ""
            asker = row["owner_name"] or row["client"] or "someone"
            via = f" (through {row['client']})" if row["client"] and row["owner_name"] not in (None, row["client"]) else ""
            self.journal.add(row["id"], f"Asked by {asker}{via}: {row['url']}, {where}{part}. "
                                        f"Options: {row['options']}." + (" Callback to " + row["callback_url"]
                                                                          if row["callback_url"] else ""))
        return row, new

    async def probe(self, url: str, destination: str | None, options: OptionsPatch,
                    section: Section | None = None, who: Principal | None = None) -> dict:
        """What the link would download (probe.py), and where it would land."""
        url = links.check(url)
        destination = self.allowed_destination(who, destination)
        await links.reachable(url)
        row, resolved, template = self.plan(destination, options)
        # Read for the whole video (a section changed doesn't ask the site again), then made the section's.
        info = await self.prober.probe(url, resolved, to_output_template(template))
        duration, size = info.get("duration"), (info.get("format") or {}).get("size_bytes")
        if section and duration and size:
            # The part's share of the whole: a size worked out, not the site's.
            share = max(0.0, min(section.end, duration) - section.start) / duration
            info = {**info, "format": {**info["format"], "size_bytes": int(size * share), "size_exact": False}}
        if section and info.get("filename"):
            stem, dot, extension = info["filename"].rpartition(".")
            info = {**info, "filename": f"{stem} [{section.label()}]{dot}{extension}" if dot else
                    f"{info['filename']} [{section.label()}]"}
        target = None
        if info.get("kind") == "video" and info.get("filename"):
            folder = row["folder"] if row else None
            path = f"{folder}/{info['filename']}" if folder else info["filename"]
            earlier = self.jobs.reusable(info["fingerprint"], json.dumps(resolved.model_dump()), folder, template,
                                         section_text(section)) if info.get("fingerprint") else None
            target = {"destination": row["slug"] if row else None, "path": path if folder else None,
                      "exists": bool(folder) and (self.config.media_root / path).is_file(),
                      "earlier": {"job_id": earlier["id"], "finished_at": earlier["finished_at"]} if earlier else None}
        return {**info, "options": resolved.model_dump(), "target": target}

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
        # A job's held files are all in held_dir/<job id>, and expire together.
        gone: dict[str, int] = {}
        for file in self.jobs.expired_held(now, forget_before):
            placement.clear(self.config.held_dir / file["job_id"])
            self.jobs.file_removed(file["job_id"], file["position"])
            gone[file["job_id"]] = file["job_hold"] or settings.hold_hours
        for job_id, hours in gone.items():
            self.journal.add(job_id, f"Its files were removed: they are kept {hours} h after the download. "
                                     "Download again (POST …/retry) to have them back.")
        if gone:
            log.info("removed the held files of %d downloads, past their hold", len(gone))
        if forget_before:
            forgotten = self.jobs.forget(forget_before)
            if forgotten:
                log.info("forgot %d downloads older than %d days", forgotten, settings.keep_days)
        self.previews.sweep([path for path in self.jobs.every_file() if path.is_file()])

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
            wanted = [{"name": "Download problems", "type": "apprise",
                       "config": {"url": self.config.apprise_url, "tag": self.config.apprise_tag or None}}]
            if self.config.apprise_finished_tag:
                wanted.append({"name": "Finished downloads", "type": "apprise", "events": ["job.finished"],
                               "config": {"url": self.config.apprise_url, "tag": self.config.apprise_finished_tag}})
            for item in wanted:
                try:
                    self.notifications.create(item)
                except Invalid as exc:
                    log.warning("no %s notification from the environment: %s", item["name"], exc)
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

    journal = Journal(db)
    jobs.hold_from(lambda: settings().hold_hours)
    callbacks = Callbacks(http, lambda job_id, last: _record_callback(jobs, journal, job_id, last))
    # The downloads' and link checks' only way out (batlab_common/egress.py).
    egress = Proxy(name="videodrop")
    proxy = egress.start()
    scheduler = Scheduler(config, jobs, settings, notifier.emit, callbacks, broker.publish, journal, proxy)
    jobs.live_from(scheduler.live)
    jobs.on_change(broker.publish)
    jobs.on_change(scheduler.wake)
    destinations.on_remove(settings.forget_destination)
    releases = ReleaseWatch(db, http)
    issues = Issues(config, db, settings, scheduler, notifications, releases)
    previews = Previews(config.previews_dir)
    # A finished video's gallery and storyboard are made at once, ready when someone looks.
    scheduler.on_finished(lambda job_id: previews.prepare(
        [jobs.file_path(f) for f in jobs.files(job_id) if f["kind"] == "video"]))
    return Services(config=config, db=db, http=http, settings=settings, destinations=destinations, jobs=jobs,
                    notifications=notifications, notifier=notifier, broker=broker, callbacks=callbacks,
                    scheduler=scheduler, prober=Prober(config.state_dir, config.deno, config.prober, proxy),
                    releases=releases, issues=issues, previews=previews, journal=journal,
                    sites=Sites(config.state_dir, http), egress=egress, accounts=open_accounts(db))


def _record_callback(jobs: JobStore, journal: Journal, job_id: str, last: dict) -> None:
    with suppress(NotFound):
        jobs.row(job_id)
        jobs.update(job_id, callback_last=last)
        journal.add(job_id, f"Callback {last['event']}, try {last['tries']}: {last['message']}",
                    "info" if last["ok"] else "warning", "callback")
