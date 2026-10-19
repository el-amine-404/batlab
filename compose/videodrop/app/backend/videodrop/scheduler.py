"""Runs the queued jobs: as many at once as the settings allow (max_parallel,
and per_site_parallel for each site), the highest priority first, then the
oldest. Each one is a process of its own (runner.py), followed through the
events it prints: its progress is kept here, live, and saved now and then.

A finished download's files go where they belong (placement.py). A download
that fails for a reason that may pass (the network, the site busy) is tried
again later, waiting longer each time, up to the settings' retries; then it
has failed (job.failed). Nothing starts while the media disk is fuller than
min_free_gb (disk.low, once). A process silent for STALLED is stopped.

Each job's log (journal.py) says what happened to it: each try, what
yt-dlp said and what the process printed besides, the fingerprint, each
step and how long it took, where its files went.

It never stops: a job going wrong is logged and the others go on."""

import asyncio
import json
import logging
import os
import re
import signal
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from . import cookies, folders, placement
from .callbacks import Callbacks
from .config import Config
from .events import Event
from .jobs import JobStore
from .journal import Journal
from .loops import call_on
from .options import Section, to_output_template
from .settings import Settings

log = logging.getLogger("videodrop.scheduler")

TICK = 5
SAVE_EVERY = 5
PUBLISH_EVERY = 1
CALLBACK_PROGRESS_EVERY = 10
STALLED = 30 * 60
STOP_GRACE = 10
MAX_BACKOFF = 6 * 3600
# What a runner may print on one line (a long error with a URL in it).
LINE_LIMIT = 1 << 20
# Lines of a run's log from its process (yt-dlp's, and what it printed besides), at most.
LOGGED_LINES = 1500

# Failures that may pass: tried again later.
TRANSIENT = re.compile(
    r"HTTP Error (?:5\d\d|429)|timed? ?out|Connection (?:reset|refused|aborted)|Temporary failure|"
    r"Network is unreachable|IncompleteRead|Unable to download (?:webpage|API page|JSON)|Remote end closed|"
    r"confirm you.re not a bot|rate.limit|The download stalled|ffmpeg exited", re.I)


@dataclass
class Run:
    """A job running now."""

    job_id: str
    site: str
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task | None = None
    progress: dict = field(default_factory=dict)
    made: list[str] = field(default_factory=list)
    # Each made file's check (checks.py), by its path in the work folder.
    checks: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # The earlier job whose file this one reuses (the site serves the same video).
    reused: str | None = None
    cancelled: bool = False
    stopping: bool = False
    saved_at: float = 0.0
    published_at: float = 0.0
    called_at: float = 0.0
    began: float = field(default_factory=time.monotonic)
    # The stage or step going on, and since when (monotonic): its time is logged when the next begins.
    doing: str | None = None
    doing_since: float = 0.0
    logged: int = 0
    # The last lines the process printed besides its events (a crash's traceback).
    printed: list[str] = field(default_factory=list)


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _whole(value) -> int | None:
    """Sites give sizes as estimates with decimals; bytes are whole."""
    number = _number(value)
    return round(number) if number is not None else None


def section_of(row) -> Section | None:
    if not row["section"]:
        return None
    start, end = json.loads(row["section"])
    return Section(start=start, end=end)


def _size(count: float | None) -> str:
    if not count:
        return "?"
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if count >= scale:
            return f"{count / scale:.1f} {unit}"
    return f"{int(count)} B"


def backoff(attempt: int) -> float:
    """1 min, 5 min, 25 min…, 6 h at most."""
    return min(MAX_BACKOFF, 60 * 5 ** max(0, attempt - 1))


class Scheduler:
    def __init__(self, config: Config, jobs: JobStore, settings: Callable[[], Settings],
                 emit: Callable[[Event], None], callbacks: Callbacks, publish: Callable[[str], None],
                 journal: Journal, proxy: str | None = None):
        self._config = config
        # Where the download processes connect through (egress.py); None in the tests' fake ones.
        self._proxy = proxy
        self._jobs = jobs
        self._settings = settings
        self._emit = emit
        self._callbacks = callbacks
        self._publish = publish
        self._journal = journal
        self._runs: dict[str, Run] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.disk_low = False
        self.free_bytes: int | None = None
        self._finished: list[Callable[[str], None]] = []

    def on_finished(self, listener: Callable[[str], None]) -> None:
        """listener(job_id) once a job has finished with files (from a thread
        of the event loop: it must return at once)."""
        self._finished.append(listener)

    # ---- what the API and the store see -------------------------------------------------

    def live(self, job_id: str) -> dict | None:
        run = self._runs.get(job_id)
        return dict(run.progress) if run else None

    @property
    def running(self) -> int:
        return len(self._runs)

    def wake(self, _job_id: str | None = None) -> None:
        """Look at the queue now. Safe from any thread (a route runs in a
        worker thread)."""
        call_on(self._loop, self._wake.set)

    def cancel(self, job_id: str) -> None:
        """A queued job ends now; a running one when its process has stopped.
        On the server's loop (an async route)."""
        row = self._jobs.row(job_id)
        if row["status"] == "queued":
            self._jobs.update(job_id, status="cancelled", stage=None, retry_at=None, finished_at=time.time())
            self._journal.add(job_id, "Cancelled before it started.")
            self._tell(job_id, "cancelled")
        elif row["status"] == "running" and job_id in self._runs:
            run = self._runs[job_id]
            run.cancelled = True
            self._journal.add(job_id, "Cancel asked: its process is stopped.")
            self._stop(run)

    # ---- the loop -----------------------------------------------------------------------------

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        requeued = self._jobs.requeue_running()
        if requeued:
            log.info("%d downloads cut short by a restart are queued again", requeued)
        placement.clear(self._config.work_dir)
        self._config.work_dir.mkdir(parents=True, exist_ok=True)
        self._config.held_dir.mkdir(parents=True, exist_ok=True)
        self._task = self._loop.create_task(self._run_loop(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        for run in list(self._runs.values()):
            run.stopping = True
            self._stop(run)
        tasks = [run.task for run in self._runs.values() if run.task]
        if tasks:
            await asyncio.wait(tasks, timeout=STOP_GRACE + 5)
        # What was running starts again at the next start (requeue_running).

    async def _run_loop(self) -> None:
        while True:
            self._wake.clear()
            try:
                self._fill()
            except Exception:
                log.exception("starting downloads failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), TICK)

    def _fill(self) -> None:
        settings = self._settings()
        room = settings.max_parallel - len(self._runs)
        if room <= 0:
            return
        if not self._disk_ok(settings):
            return
        busy: dict[str, int] = {}
        for run in self._runs.values():
            busy[run.site] = busy.get(run.site, 0) + 1
        full = {site for site, count in busy.items() if count >= settings.per_site_parallel}
        for row in self._jobs.next_queued(time.time(), full, room * 4):
            if room <= 0:
                break
            if busy.get(row["site"], 0) >= settings.per_site_parallel:
                continue
            busy[row["site"]] = busy.get(row["site"], 0) + 1
            room -= 1
            run = Run(row["id"], row["site"])
            self._runs[row["id"]] = run
            self._jobs.update(row["id"], status="running", stage="starting", attempts=row["attempts"] + 1,
                              retry_at=None, error=None, progress={}, notes=[],
                              started_at=row["started_at"] or time.time())
            run.task = asyncio.get_running_loop().create_task(self._run(run), name=f"job-{row['id'][:8]}")

    def _disk_ok(self, settings: Settings) -> bool:
        self.free_bytes = folders.free_bytes(self._config.media_root)
        low = self.free_bytes is not None and self.free_bytes < settings.min_free_gb * 1024 ** 3
        if low and not self.disk_low:
            self._emit(Event("disk.low", "videodrop: the media disk is nearly full",
                             (f"{self.free_bytes / 1024 ** 3:.1f} GB free, under the {settings.min_free_gb:g} GB "
                              "Settings ask for. Downloads wait until there is room.",),
                             self._page(settings, "/settings/status"), {"free_bytes": self.free_bytes}))
        self.disk_low = low
        return not low

    # ---- one job ------------------------------------------------------------------------------------

    async def _run(self, run: Run) -> None:
        job_id = run.job_id
        work = self._config.work_dir / job_id
        try:
            self._tell(job_id, "started")
            row = self._jobs.row(job_id)
            await asyncio.to_thread(placement.clear, work)
            work.mkdir(parents=True)
            settings = self._settings()
            self._journal.add(job_id, f"Try {row['attempts']} of {settings.retries + 1} starts"
                                      f"{' (it was waiting for a retry)' if row['error'] else ''}.")
            request = {
                "url": row["url"],
                "work_dir": str(work),
                "options": json.loads(row["options"]),
                "output": to_output_template(row["template"], section_of(row)),
                "section": json.loads(row["section"]) if row["section"] else None,
                "rate_limit": settings.rate_limit_mib * 1024 ** 2 or None,
                "max_filesize": settings.max_filesize_gb * 1024 ** 3 or None,
                "cookies": cookies.copy_for(self._config.state_dir, work),
                "deno": self._config.deno,
                "song": json.loads(row["song"]) if row["song"] else None,
                "proxy": self._proxy,
            }
            limits = [f"rate limit {settings.rate_limit_mib} MiB/s" if settings.rate_limit_mib else None,
                      f"files up to {settings.max_filesize_gb} GB" if settings.max_filesize_gb else None,
                      "with the saved YouTube cookies" if request["cookies"] else None]
            self._journal.add(job_id, "Its process starts: yt-dlp, output " + request["output"]
                              + "".join(f", {limit}" for limit in limits if limit) + ".", "debug")
            await self._follow(run, work, request)
            await self._end(run, row, work)
        except Exception as exc:
            log.exception("download %s", job_id)
            self._journal.add(job_id, f"videodrop failed while running it: {exc.__class__.__name__}: {exc}", "error")
            run.errors.append(f"videodrop failed: {exc.__class__.__name__}.")
            with suppress(Exception):
                self._finish(run, self._jobs.row(job_id), [])
        finally:
            self._runs.pop(job_id, None)
            with suppress(Exception):
                self._journal.trim(job_id)
            await asyncio.to_thread(placement.clear, work)
            self.wake()

    async def _follow(self, run: Run, work: Path, request: dict) -> None:
        """Starts the process and reads its events until it ends."""
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "HOME": str(work), "XDG_CACHE_HOME": str(work / ".cache"),
               "DENO_DIR": str(work / ".deno")}
        run.process = await asyncio.create_subprocess_exec(
            *self._config.runner, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=work, env=env, start_new_session=True, limit=LINE_LIMIT)
        printed = asyncio.get_running_loop().create_task(self._read_printed(run))
        # Open until the process ends: it asks once more (the fingerprint).
        run.process.stdin.write(json.dumps(request).encode() + b"\n")
        await run.process.stdin.drain()
        while True:
            try:
                line = await asyncio.wait_for(run.process.stdout.readline(), STALLED)
            except TimeoutError:
                run.errors.append(f"The download stalled: nothing happened for {STALLED // 60} minutes.")
                self._journal.add(run.job_id, run.errors[-1] + " Its process is stopped.", "error")
                self._stop(run)
                break
            except ValueError:  # a line over LINE_LIMIT
                continue
            if not line:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") == "fingerprint":
                await self._answer(run, event.get("value"))
            self._on_event(run, event)
        with suppress(Exception):
            run.process.stdin.close()
        code = await run.process.wait()
        with suppress(TimeoutError):
            await asyncio.wait_for(printed, 5)
        self._doing(run, None)
        self._journal.add(run.job_id, f"Its process ended (exit {code}) after "
                                      f"{time.monotonic() - run.began:.1f} s.", "debug" if code == 0 else "info")
        if code not in (0, 1) and not run.cancelled and not run.stopping:
            # Not a download failing (1): the process itself did.
            log.warning("download %s: its process ended with %s; it printed: %s", run.job_id, code,
                        " | ".join(run.printed[-5:]) or "nothing")

    async def _read_printed(self, run: Run) -> None:
        """What the process prints besides its events (a library's warning, a
        crash's traceback): into the job's log, as it comes."""
        while True:
            try:
                line = await run.process.stderr.readline()
            except ValueError:
                continue
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip()
            if not text:
                continue
            run.printed = [*run.printed[-19:], text[:500]]
            self._log_line(run, text, "warning", "process")

    def _log_line(self, run: Run, message: str, level, source) -> None:
        """A line from the process, LOGGED_LINES a run at most."""
        run.logged += 1
        if run.logged < LOGGED_LINES:
            self._journal.add(run.job_id, message, level, source)
        elif run.logged == LOGGED_LINES:
            self._journal.add(run.job_id, "More lines came from its process; they are not kept.", "warning")

    async def _answer(self, run: Run, fingerprint: str | None) -> None:
        """Whether an earlier download got the same file: then that one is
        reused and nothing is downloaded (unless the job asked again)."""
        row = self._jobs.row(run.job_id)
        self._jobs.update(run.job_id, fingerprint=fingerprint)
        earlier = None
        if fingerprint and not row["again"]:
            earlier = self._jobs.reusable(fingerprint, row["options"], row["folder"], row["template"], row["section"],
                                          row["id"])
        run.reused = earlier["id"] if earlier else None
        if not fingerprint:
            self._journal.add(run.job_id, "No fingerprint: the site says too little of the file to tell it from "
                                          "another. It is downloaded.")
        elif earlier:
            self._journal.add(run.job_id, f"Fingerprint {fingerprint[:16]}…: download {earlier['id']} got the same "
                                          "file, with the same options, and it's still there. It is reused.")
        else:
            why = ("asked to download again" if row["again"] else
                   "no earlier download with the same options and place has it")
            self._journal.add(run.job_id, f"Fingerprint {fingerprint[:16]}…: {why}. It is downloaded.")
        with suppress(ConnectionError, BrokenPipeError):
            run.process.stdin.write(json.dumps({"reuse": run.reused}).encode() + b"\n")
            await run.process.stdin.drain()

    def _on_event(self, run: Run, event: dict) -> None:
        name, now = event.get("event"), time.monotonic()
        progress = run.progress
        if name == "item":
            self._journal.add(run.job_id, f"Video {event['index'] + 1}"
                                          f"{' of ' + str(event['total']) if (event.get('total') or 1) > 1 else ''}: "
                                          f"{event.get('title') or event.get('id') or '?'}"
                                          f"{' (' + str(round(event['duration'])) + ' s)' if isinstance(event.get('duration'), (int, float)) else ''}.")
            progress.update(item=event["index"] + 1, items=max(event.get("total") or 1, event["index"] + 1),
                            item_title=event.get("title"))
            if event["index"] == 0:
                self._jobs.update(run.job_id, title=(event.get("title") or "")[:300] or None,
                                  uploader=(event.get("uploader") or "")[:200] or None,
                                  thumbnail=event.get("thumbnail") if str(event.get("thumbnail") or "").startswith(
                                      "https://") else None,
                                  duration=event.get("duration") if isinstance(event.get("duration"),
                                                                               (int, float)) else None)
        elif name == "progress":
            total, done = _whole(event.get("total")), _whole(event.get("downloaded")) or 0
            fragment, fragments = _whole(event.get("fragment")), _whole(event.get("fragments"))
            # Without a size (a live-made stream), its pieces say how far it is.
            fraction = (min(1.0, done / total) if total else
                        min(1.0, fragment / fragments) if fragment and fragments else None)
            items, item = progress.get("items") or 1, (progress.get("item") or 1) - 1
            progress.update(downloaded_bytes=done, total_bytes=total, speed=_number(event.get("speed")),
                            eta=_number(event.get("eta")),
                            percent=round(100 * (item + (fraction or 0)) / items, 1) if fraction is not None
                            else progress.get("percent"))
            if progress.get("stage") != "downloading":
                progress.update(stage="downloading", step=None, step_since=time.time())
                self._doing(run, "downloading")
        elif name == "stage":
            if (event.get("stage"), event.get("step")) != (progress.get("stage"), progress.get("step")):
                progress.update(stage=event.get("stage"), step=event.get("step"), step_since=time.time(),
                                step_percent=None)
                self._doing(run, event.get("step") or event.get("stage"))
                self._jobs.update(run.job_id, stage=event.get("stage"), progress=progress)
        elif name == "file":
            run.made.append(event["path"])
            run.checks[event["path"]] = event.get("checks")
            checks = event.get("checks") or {}
            self._journal.add(run.job_id, f"Made {event['path']}: checked, {checks.get('summary') or '?'}"
                                          + "".join(f" · {problem}" for problem in checks.get("problems") or []))
        elif name == "step":
            progress["step_percent"] = _number(event.get("percent"))
        elif name == "note":
            run.notes.append(str(event.get("message") or "")[:500])
            self._jobs.update(run.job_id, notes=run.notes)
            self._journal.add(run.job_id, run.notes[-1])
        elif name == "log":
            self._log_line(run, str(event.get("message") or ""), event.get("level") or "info", "yt-dlp")
        elif name == "warning":
            self._log_line(run, str(event.get("message") or ""), "warning", "yt-dlp")
        elif name == "error":
            run.errors.append(str(event.get("message") or "")[:1000])
            self._log_line(run, run.errors[-1], "error", "yt-dlp")
        if now - run.saved_at >= SAVE_EVERY:
            run.saved_at = now
            self._jobs.update(run.job_id, progress=progress, stage=progress.get("stage") or "downloading")
        elif now - run.published_at >= PUBLISH_EVERY:
            run.published_at = now
            self._publish(run.job_id)
        if name == "progress" and now - run.called_at >= CALLBACK_PROGRESS_EVERY:
            run.called_at = now
            self._tell(run.job_id, "progress")

    def _doing(self, run: Run, what: str | None) -> None:
        """A stage or step begins: the one before took this long (and, the
        download, at what speed)."""
        now = time.monotonic()
        if run.doing and run.doing != what:
            took = now - run.doing_since
            if run.doing == "downloading":
                done = run.progress.get("downloaded_bytes")
                if done:
                    self._journal.add(run.job_id, f"Downloaded {_size(done)} in {took:.1f} s, "
                                                  f"{_size(done / max(took, 0.001))}/s on average.")
                else:
                    # A part of a video: ffmpeg downloads it and says nothing until it's done.
                    self._journal.add(run.job_id, f"Downloaded in {took:.1f} s.")
            else:
                self._journal.add(run.job_id, f"{run.doing} took {took:.1f} s.", "debug")
        if what != run.doing:
            run.doing, run.doing_since = what, now
            if what and what != "downloading":
                self._journal.add(run.job_id, f"Step: {what}.", "debug")

    async def _end(self, run: Run, row, work: Path) -> None:
        self._doing(run, None)
        if run.stopping:
            return  # videodrop is stopping: it runs again at the next start
        if run.cancelled:
            self._jobs.update(run.job_id, status="cancelled", stage=None, progress={}, finished_at=time.time())
            self._journal.add(run.job_id, "Cancelled: what it had downloaded is dropped.")
            self._tell(run.job_id, "cancelled")
            return
        placed = []
        if run.reused:
            placed = await asyncio.to_thread(self._reuse, run, row)
            if placed:
                earlier = self._jobs.row(run.reused)
                when = time.strftime("%d %b %Y at %H:%M", time.localtime(earlier["finished_at"]))
                run.notes.append(f"The site serves the same video as for the download of {when}, with the same "
                                 "options: its file is reused, nothing was downloaded.")
                self._jobs.update(run.job_id, notes=run.notes, reused_from=run.reused)
                self._journal.add(run.job_id, run.notes[-1])
                self._jobs.add_files(run.job_id, placed)
        elif run.made:
            run.progress.update(stage="placing", step=None, step_since=time.time())
            self._jobs.update(run.job_id, stage="placing", progress=run.progress)
            placed = await asyncio.to_thread(placement.place, work, run.made, self._target(row))
            for file in placed:
                # A copy that was there already keeps no check of the dropped one.
                file["checks"] = None if file["existed"] else run.checks.get(file["source"])
                where = f"kept here as {file['path']}" if file["root"] == "held" else f"saved to {file['path']}"
                self._journal.add(run.job_id, f"{file['name']} ({_size(file['size'])}): "
                                              + ("the destination had it already, the new copy is dropped."
                                                 if file["existed"] else f"{where}."))
            self._jobs.add_files(run.job_id, placed)
        self._finish(run, row, placed)

    def _reuse(self, run: Run, row) -> list[dict]:
        """The earlier job's files as this job's: the same place in a
        destination; a held file linked into this job's own held folder (its
        own lifetime, no second copy on the disk)."""
        placed = []
        for file in self._jobs.files(run.reused):
            source = self._jobs.file_path(file)
            if not source.is_file():
                return []
            path = file["path"]
            if file["root"] == "held":
                path = f"{row['id']}/{file['path'].split('/', 1)[1]}"
                target = self._config.held_dir / path
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(source, target)
                except FileExistsError:
                    pass
            placed.append({"root": file["root"], "path": path, "name": file["name"], "size": file["size"],
                           "kind": file["kind"], "existed": True,
                           "checks": json.loads(file["checks"]) if file["checks"] else None})
        return placed

    def _target(self, row) -> placement.Target:
        if row["folder"]:
            return placement.Target("destination", self._config.media_root, row["folder"])
        return placement.Target("held", self._config.held_dir, row["id"])

    def _finish(self, run: Run, row, placed: list[dict]) -> None:
        settings = self._settings()
        now = time.time()
        if placed:
            failed = len(run.errors)
            note = f"{failed} of the videos failed; the last: {run.errors[-1]}" if failed else None
            self._jobs.update(run.job_id, status="finished", stage=None, progress={}, error=note, finished_at=now)
            held = [file for file in placed if file["root"] == "held"]
            kept = f" Its files are kept {self._jobs.hold_hours(row)} h." if held else ""
            self._journal.add(run.job_id, f"Finished in {time.monotonic() - run.began:.1f} s.{kept}")
            self._tell(run.job_id, "finished")
            for listener in self._finished:
                try:
                    listener(run.job_id)
                except Exception:
                    log.exception("after download %s", run.job_id)
            return
        reason = run.errors[-1] if run.errors else "The download failed without saying why."
        reason = re.sub(r"^ERROR:\s*", "", reason)
        attempts = self._jobs.row(run.job_id)["attempts"]
        if TRANSIENT.search(reason) and attempts <= settings.retries:
            wait = backoff(attempts)
            log.info("download %s failed (%s); again in %d s", run.job_id, reason, wait)
            self._journal.add(run.job_id, f"This try failed, for a reason that may pass: {reason} Tried again in "
                                          f"{wait // 60:.0f} min.", "warning")
            self._jobs.update(run.job_id, status="queued", stage=None, progress={}, error=reason,
                              retry_at=now + wait)
            return
        self._jobs.update(run.job_id, status="failed", stage=None, progress={}, error=reason, finished_at=now)
        self._journal.add(run.job_id, f"Failed: {reason}"
                                      + (f" (after {attempts} tries)" if TRANSIENT.search(reason) else
                                         " (not a reason that passes: not tried again)"), "error")
        self._tell(run.job_id, "failed")

    def _stop(self, run: Run) -> None:
        """Its process and everything it started (ffmpeg): asked, then made to."""
        process = run.process
        if process is None or process.returncode is not None:
            return
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)

        async def force():
            await asyncio.sleep(STOP_GRACE)
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)

        asyncio.get_running_loop().create_task(force())

    # ---- telling --------------------------------------------------------------------------------------

    def _page(self, settings: Settings, path: str) -> str | None:
        return f"{settings.public_url}{path}" if settings.public_url else None

    def _tell(self, job_id: str, event: str) -> None:
        """The job's caller (its callback) and, at its end, the notifications."""
        row = self._jobs.row(job_id)
        snapshot = self._jobs.public(row)
        if row["callback_url"] and event in json.loads(row["callback_events"]):
            self._callbacks.send(row["callback_url"], row["callback_secret"], event, snapshot)
        if event not in ("finished", "failed"):
            return
        settings = self._settings()
        title = row["title"] or row["url"]
        if event == "failed":
            lines = (row["error"] or "No reason given.", f"Link: {row['url']}",
                     *([f"Asked by {row['client']}"] if row["client"] else ()))
            self._emit(Event("job.failed", f"Download failed: {title[:150]}", lines,
                             self._page(settings, f"/jobs/{job_id}"), {"job": snapshot}))
        else:
            where = (f"Into {row['folder']}." if row["folder"]
                     else f"Kept {self._jobs.hold_hours(row)} h, to download from its page.")
            files = [f["name"] for f in snapshot["files"] if f["kind"] == "video"]
            lines = (where, *files[:5], *([f"Asked by {row['client']}"] if row["client"] else ()))
            self._emit(Event("job.finished", f"Downloaded: {title[:150]}", lines,
                             self._page(settings, f"/jobs/{job_id}"), {"job": snapshot}))

