"""Jobs: one per pasted link, run one after another by a worker thread.

A job runs its engine's runner (runners/) as a child process in the job's own
folder, reads the runner's events, and copies every finished song into
musicguard's inbox, where it is checked, tagged and filed into the library.
The job's own copy stays downloadable for SONGDROP_RETENTION_HOURS, then the
folder is removed. Jobs survive a restart (job.json); one that was running is
marked interrupted, queued ones run again.
"""

import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
import zipfile
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import cookies, messages, outbound, problems
from . import options as opts
from .config import Settings
from .detect import Detection
from .events import Event

log = logging.getLogger("songdrop.jobs")

RUNNERS = Path(__file__).resolve().parent / "runners"
ACTIVE = ("queued", "running")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
FILE_ID = re.compile(r"^[0-9a-f]{12}$")
JOB_FILE = "job.json"
# Folders a runner writes besides the songs; removed when the job ends.
SCRATCH = (".tmp", ".cache")
ARCHIVE_DIR = ".archive"
KILL_GRACE_SECONDS = 10
JANITOR_INTERVAL_SECONDS = 300
# A song that failed is tried again on its own, later (settings.retry_minutes,
# 10 min, 1 h and 6 h unless changed): YouTube's 403s come in bursts (an
# album's songs fetched three at a time), and pass. After the last delay,
# songdrop gives up until someone presses "Retry now".
# Worker threads always running; settings.concurrent_jobs of them may work.
WORKERS = 4
RETRY_CHECK_SECONDS = 20
# Waiting for a later try, queued for one, or in one.
PENDING = ("waiting", "queued", "retrying")


class JobError(problems.Problem):
    """No such job, or not now (a running job can't be removed…)."""


@dataclass
class Job:
    id: str
    url: str
    engine: str
    site: str
    kind: str
    options: dict
    created_at: float
    status: str = "queued"  # queued, running, done, partial, failed, cancelled
    title: str | None = None
    total: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    message: str | None = None
    files: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    # What is worth knowing besides: a wrong YouTube upload replaced.
    notes: list = field(default_factory=list)
    # Told the job (as GET /api/v1/jobs/{id} gives it) when it ends, and when
    # a later try ends.
    callback_url: str | None = None
    # Songs in progress, index -> {"name", "percent"}; not saved.
    current: dict = field(default_factory=dict, repr=False)

    def saved(self) -> dict:
        data = asdict(self)
        data.pop("current")
        return data


def _safe_name(text: str, fallback: str) -> str:
    cleaned = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', " ", text or "").strip(" .")
    return re.sub(r"\s+", " ", cleaned)[:120] or fallback


class JobManager:
    def __init__(self, settings: Settings, emit: Callable[[Event], None] = lambda event: None):
        self.settings = settings
        # Where events go (connections.Connections.emit); jobs never pick a channel.
        self._emit = emit
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._deliver_lock = threading.Lock()
        self._archive_lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._processes: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []
        self._busy = 0  # workers on a job or a retry now

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        if self.settings.deliver and not self.settings.output_dir.is_dir():
            log.warning("output folder %s is missing; it is made with the first song", self.settings.output_dir)
        self._load()
        for number in range(WORKERS):
            self._spawn(self._work, f"worker-{number}")
        self._spawn(self._janitor, "janitor")
        self._spawn(self._retrier, "retrier")

    def stop(self) -> None:
        self._stopping.set()
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            self._signal(process, signal.SIGTERM)

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _load(self) -> None:
        requeue = []
        for path in sorted(self.settings.jobs_dir.glob(f"*/{JOB_FILE}")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                job = Job(**data)
            except Exception:
                log.exception("ignoring unreadable %s", path)
                continue
            if job.status == "running":
                job.status = "failed" if not job.files else "partial"
                job.message = "Interrupted: songdrop restarted while this was downloading."
                job.finished_at = time.time()
                job.current = {}
                self._save(job)
            elif job.status == "queued":
                requeue.append(job)
            for entry in job.errors:
                if entry.get("state") in ("queued", "retrying"):
                    entry["state"] = "waiting"
                    entry["next_retry_at"] = time.time() + 60
            self._jobs[job.id] = job
        for job in sorted(requeue, key=lambda item: item.created_at):
            self._queue.put(job.id)
        log.info("loaded %d job(s), %d queued again", len(self._jobs), len(requeue))

    # ---- public API ------------------------------------------------------

    @property
    def max_tries(self) -> int:
        return 1 + len(self.settings.retry_minutes)

    def submit(self, detection: Detection, options, callback_url: str | None = None) -> dict:
        job = Job(
            id=uuid.uuid4().hex,
            url=detection.url,
            engine=detection.engine,
            site=detection.site,
            kind=detection.kind,
            options=options.model_dump(),
            created_at=time.time(),
            callback_url=callback_url,
        )
        self._dir(job.id).mkdir(parents=True)
        with self._lock:
            self._jobs[job.id] = job
            self._save(job)
            snapshot = self._public(job)
        self._queue.put(job.id)
        return snapshot

    def list(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
            return [self._public(job) for job in jobs]

    def get(self, job_id: str) -> dict:
        with self._lock:
            return self._public(self._find(job_id))

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self._find(job_id)
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = time.time()
                self._save(job)
            elif job.status == "running":
                self._cancelled.add(job.id)
                process = self._processes.get(job.id)
                if process is not None:
                    self._stop_process(process)
            return self._public(job)

    def retry(self, job_id: str, index: int) -> dict:
        """Try a missing song again now, even one songdrop gave up on."""
        with self._lock:
            job = self._find(job_id)
            entry = next((item for item in job.errors if item.get("index") == index), None)
            if entry is None or not entry.get("url"):
                raise JobError(404, "This song can't be tried again.")
            if job.status in ACTIVE:
                raise JobError(409, "Wait until the download has finished.")
            if entry.get("state") in ("queued", "retrying"):
                return self._public(job)
            entry["state"] = "waiting"
            entry["next_retry_at"] = time.time()
            self._save(job)
            return self._public(job)

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._find(job_id)
            if job.status in ACTIVE:
                raise JobError(409, "Cancel it first; it's still running.")
            del self._jobs[job.id]
        shutil.rmtree(self._dir(job.id), ignore_errors=True)

    def file(self, job_id: str, file_id: str) -> tuple[Path, str]:
        if not FILE_ID.match(file_id):
            raise JobError(404, "No such file.")
        with self._lock:
            job = self._find(job_id)
            entry = next((item for item in job.files if item["id"] == file_id), None)
        if entry is None:
            raise JobError(404, "No such file.")
        path = self._inside(job.id, entry["rel"])
        if path is None or not path.is_file():
            raise JobError(410, "This file has expired.")
        return path, entry["name"]

    def archive(self, job_id: str) -> tuple[Path, str]:
        """All of a finished job's songs in one zip (stored, not compressed:
        audio doesn't compress), built once."""
        with self._lock:
            job = self._find(job_id)
            if job.status in ACTIVE:
                raise JobError(409, "Wait until it has finished.")
            files = [dict(item) for item in job.files]
            name = _safe_name(job.title, "songs") + ".zip"
        if not files:
            raise JobError(404, "Nothing to download.")
        target = self._dir(job.id) / ARCHIVE_DIR / "songs.zip"
        with self._archive_lock:
            if not target.is_file():
                target.parent.mkdir(exist_ok=True)
                partial = target.with_suffix(".zip.part")
                with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED) as archive:
                    used = set()
                    for item in files:
                        path = self._inside(job.id, item["rel"])
                        if path is None or not path.is_file():
                            continue
                        arcname = item["name"]
                        stem, ext = os.path.splitext(arcname)
                        number = 2
                        while arcname in used:
                            arcname = f"{stem} ({number}){ext}"
                            number += 1
                        used.add(arcname)
                        archive.write(path, arcname)
                os.replace(partial, target)
        return target, name

    # ---- running a job ---------------------------------------------------

    def _work(self) -> None:
        while not self._stopping.is_set():
            # Only settings.concurrent_jobs workers take work at once; the
            # setting can change while songdrop runs.
            with self._lock:
                free = self._busy < self.settings.concurrent_jobs
                if free:
                    self._busy += 1
            if not free:
                self._stopping.wait(0.5)
                continue
            try:
                self._take_one()
            finally:
                with self._lock:
                    self._busy -= 1

    def _take_one(self) -> None:
        try:
            job_id = self._queue.get(timeout=1)
        except queue.Empty:
            return
        if ":" in job_id:
            self._retry(*job_id.split(":", 1))
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "queued":
                return
            job.status = "running"
            job.started_at = time.time()
            self._save(job)
        try:
            self._run(job)
        except Exception:
            log.exception("job %s crashed", job.id)
            with self._lock:
                self._finish(job, forced="failed", message="songdrop hit an internal error; see the container log.")
                self._call_back(job)

    def _runner_command(self, job: Job, job_dir: Path, url: str | None = None) -> tuple[list[str], dict]:
        settings = self.settings
        model = opts.parse(job.engine, job.options)
        url = url or job.url
        job_dir.mkdir(parents=True, exist_ok=True)
        # A private copy, removed with the job's other leftovers (_tidy).
        cookie_file = cookies.copy_for(settings.state_dir, job_dir)
        if job.engine == "spotdl":
            config = {
                "url": url,
                "settings": opts.spotdl_settings(model, str(job_dir), settings.spotdl_parallel, cookie_file),
                "max_items": settings.max_items,
                "parallel": settings.spotdl_parallel,
            }
            return [settings.spotdl_python, str(RUNNERS / "spotdl_runner.py")], config
        config = {
            "url": url,
            "job_dir": str(job_dir),
            "settings": opts.ytdlp_settings(model),
            "max_items": settings.max_items,
            "deno": settings.deno,
            "cookies": cookie_file,
        }
        return [settings.app_python, str(RUNNERS / "ytdlp_runner.py")], config

    def _run(self, job: Job) -> None:
        job_dir = self._dir(job.id)
        job_dir.mkdir(parents=True, exist_ok=True)
        command, config = self._runner_command(job, job_dir)
        fatal, returncode, output, timed_out = self._execute(
            job, job_dir, command, config, lambda event: self._apply(job, job_dir, event))
        with self._lock:
            if job.id in self._cancelled:
                self._cancelled.discard(job.id)
                self._finish(job, forced="cancelled", message="Cancelled.")
            elif timed_out:
                hours = self.settings.job_timeout_seconds / 3600
                self._finish(job, forced="failed", message=f"Stopped after {hours:g} hours.")
            elif fatal is not None:
                self._finish(job, forced="failed", message=messages.friendly(fatal))
            elif returncode != 0 and not job.files:
                last = next((line for line in reversed(output) if line.strip()), "")
                log.warning("job %s: runner exited %s; last output:\n%s", job.id, returncode, "\n".join(output))
                self._finish(job, forced="failed", message=messages.friendly(last) if last else "The download stopped unexpectedly.")
            else:
                if job.errors:
                    log.info("job %s: %d song(s) failed; engine output:\n%s", job.id, len(job.errors), "\n".join(output))
                self._finish(job)
            self._schedule_retries(job)
            self._announce(job)
            self._call_back(job)

    def _execute(self, job: Job, job_dir: Path, command: list[str], config: dict, on_event):
        """Runs a runner; on_event(event) is called under the lock for each
        event but "fatal". Returns (fatal message, exit code, last output lines,
        timed out)."""
        read_fd, write_fd = os.pipe()
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "TZ": os.environ.get("TZ", "UTC"),
            "LANG": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "SONGDROP_EVENTS_FD": str(write_fd),
        }
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                pass_fds=(write_fd,),
                env=environment,
                cwd=job_dir,
                start_new_session=True,  # its own process group, killed as one
            )
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)

        output = deque(maxlen=200)
        drain = threading.Thread(target=self._drain, args=(job.id, process, output), daemon=True)
        drain.start()
        timed_out = threading.Event()

        def on_timeout() -> None:
            timed_out.set()
            self._stop_process(process)

        timer = threading.Timer(self.settings.job_timeout_seconds, on_timeout)
        timer.daemon = True
        with self._lock:
            self._processes[job.id] = process
            if job.id in self._cancelled:
                self._stop_process(process)
        timer.start()

        fatal = None
        try:
            try:
                process.stdin.write(json.dumps(config).encode())
                process.stdin.close()
            except BrokenPipeError:
                pass
            with os.fdopen(read_fd, encoding="utf-8", errors="replace") as events:
                for line in events:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "fatal":
                        fatal = str(event.get("message") or "")
                        continue
                    with self._lock:
                        on_event(event)
            process.wait()
        finally:
            timer.cancel()
            drain.join(timeout=5)
            with self._lock:
                self._processes.pop(job.id, None)
        return fatal, process.returncode, output, timed_out.is_set()

    # ---- songs tried again later ---------------------------------------------

    def _retrier(self) -> None:
        while not self._stopping.wait(RETRY_CHECK_SECONDS):
            try:
                self._queue_due_retries()
            except Exception:
                log.exception("retry scheduling failed")

    def _queue_due_retries(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            for job in self._jobs.values():
                if job.status in ACTIVE:
                    continue
                for entry in job.errors:
                    if entry.get("state") == "waiting" and (entry.get("next_retry_at") or 0) <= now:
                        entry["state"] = "queued"
                        self._save(job)
                        self._queue.put(f"{job.id}:{entry['index']}")

    def _schedule_retries(self, job: Job) -> None:
        """After a job: every song that failed with a link of its own waits
        for its first later try. Songs skipped on purpose (explicit) do not."""
        if job.status == "cancelled":
            return
        for entry in job.errors:
            if entry.get("url") and "state" not in entry and "explicit" not in entry["message"].lower():
                delays = self.settings.retry_minutes
                if delays:
                    entry.update(state="waiting", tries=1, next_retry_at=time.time() + delays[0] * 60)
                else:  # no later tries: "Retry now" only
                    entry.update(state="gave_up", tries=1, next_retry_at=None)
        self._save(job)

    def _retry(self, job_id: str, index_text: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            entry = None
            if job is not None and job.status not in ACTIVE:
                entry = next((item for item in job.errors if str(item.get("index")) == index_text), None)
            if entry is None or entry.get("state") != "queued":
                return
            entry["state"] = "retrying"
            self._save(job)
            url, index = entry["url"], entry["index"]
        job_dir = self._dir(job.id)
        retry_dir = job_dir / f"retry-{index}-{entry.get('tries', 1)}"
        retry_dir.mkdir(parents=True, exist_ok=True)
        got = []
        failures = []

        def on_event(event: dict) -> None:
            kind = event.get("type")
            event = {**event, "index": index}
            if kind == "file":
                self._add_file(job, job_dir, event)
                got.append(event)
            elif kind == "item_error":
                failures.append(str(event.get("message") or ""))
            elif kind == "note":
                self._apply(job, job_dir, event)

        try:
            command, config = self._runner_command(job, retry_dir, url)
            fatal, _, output, _ = self._execute(job, retry_dir, command, config, on_event)
        except Exception as exc:
            log.exception("job %s: retry of song %s crashed", job.id, index)
            fatal, output = str(exc), []
        with self._lock:
            entry["tries"] = entry.get("tries", 1) + 1
            if got:
                shutil.rmtree(job_dir / ARCHIVE_DIR, ignore_errors=True)  # "Download all" is rebuilt with it
                job.errors.remove(entry)
                job.status = "partial" if job.errors else "done"
                job.message = None if job.status == "done" else job.message
                job.finished_at = time.time()  # its link lasts from now
                self._save(job)
                self._announce_retry(job, entry, got=True)
                self._call_back(job)
                return
            shutil.rmtree(retry_dir, ignore_errors=True)
            reason = failures[-1] if failures else (fatal or "The download stopped unexpectedly.")
            log.info("job %s: try %d of %s failed: %s\n%s", job.id, entry["tries"], entry["name"], reason,
                     "\n".join(output))
            entry["message"] = messages.friendly(reason)
            delays = self.settings.retry_minutes
            if entry["tries"] - 1 < len(delays):
                entry["state"] = "waiting"
                entry["next_retry_at"] = time.time() + delays[entry["tries"] - 1] * 60
            else:
                entry["state"] = "gave_up"
                entry["next_retry_at"] = None
                self._announce_retry(job, entry, got=False)
                self._call_back(job)
            self._save(job)

    # ---- events --------------------------------------------------------------

    def _link(self, page: str) -> str:
        return f"{self.settings.public_url}{page}" if self.settings.public_url else ""

    def _announce(self, job: Job) -> None:
        """After a job: job.finished, and job.incomplete when songs are missing."""
        if job.status == "cancelled":
            return
        name = job.title or job.url
        got = f"{len(job.files)} of {job.total} songs" if job.total > 1 else ("downloaded" if job.files else "not downloaded")
        lines = []
        if job.status == "failed" and job.message:
            lines.append(job.message)
        for entry in job.errors[:10]:
            later = ""
            if entry.get("state") == "waiting":
                later = f" Trying again in {_minutes(entry['next_retry_at'] - time.time())}."
            lines.append(f"- missing: {entry['name']}: {entry['message']}{later}")
        if len(job.errors) > 10:
            lines.append(f"... and {len(job.errors) - 10} more")
        for note in job.notes[:5]:
            lines.append(f"- {note['name']}: {note['message']}")
        data = {"job": self._public(job)}
        subject = f"songdrop · {name} ({got})"
        self._emit(Event("job.finished", subject, tuple(lines), self._link("/activity/history"), data))
        if job.errors:
            self._emit(Event("job.incomplete", subject, tuple(lines), self._link("/activity/missing"), data))

    def _announce_retry(self, job: Job, entry: dict, got: bool) -> None:
        name = job.title or job.url
        data = {"job": self._public(job), "song": entry["name"], "tries": entry["tries"]}
        if got:
            self._emit(Event("song.recovered", f"songdrop · got {entry['name']} on try {entry['tries']}",
                             (f"From {name}. It's on its way to the library.",), self._link("/activity/history"), data))
        else:
            self._emit(Event("song.gave_up", f"songdrop · gave up on {entry['name']} after {entry['tries']} tries",
                             (f"From {name}: {entry['message']}", "Press \"Retry now\" on the page to try once more."),
                             self._link("/activity/missing"), data))

    def _call_back(self, job: Job) -> None:
        """The caller that asked to be told (callback_url) gets the job."""
        if job.callback_url:
            outbound.in_background("callback", outbound.post_json, job.callback_url,
                                   {"event": "job.updated", "job": self._public(job)})

    def _drain(self, job_id: str, process: subprocess.Popen, output: deque) -> None:
        for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                output.append(line)
                if line.startswith("verify: "):  # which upload was kept, and why
                    log.info("job %s: %s", job_id, line[8:])
        process.stdout.close()

    def _apply(self, job: Job, job_dir: Path, event: dict) -> None:
        kind = event.get("type")
        index = event.get("index")
        if kind == "meta":
            job.title = str(event.get("title") or job.url)[:300]
            job.total = max(0, int(event.get("total") or 0))
            self._save(job)
        elif kind == "item_start":
            job.current[index] = {"name": str(event.get("name") or ""), "percent": 0.0}
        elif kind == "progress":
            if index in job.current:
                job.current[index]["percent"] = min(100.0, max(0.0, float(event.get("percent") or 0)))
        elif kind == "file":
            job.current.pop(index, None)
            self._add_file(job, job_dir, event)
            self._save(job)
        elif kind == "item_error":
            job.current.pop(index, None)
            url = event.get("url") if str(event.get("url") or "").startswith("https://") else None
            if url is None and job.total <= 1:
                url = job.url  # a single song: its link is the job's
            job.errors.append({
                "index": index,
                "name": str(event.get("name") or "")[:300],
                "message": messages.friendly(event.get("message")),
                "url": url,
            })
            self._save(job)
        elif kind == "note":
            job.notes.append({"name": str(event.get("name") or "")[:300],
                              "message": str(event.get("message") or "")[:500]})
            self._save(job)

    def _add_file(self, job: Job, job_dir: Path, event: dict) -> None:
        path = Path(str(event.get("path") or "")).resolve()
        if not path.is_file() or job_dir.resolve() not in path.parents:
            log.warning("job %s: runner reported a file outside its folder: %s", job.id, path)
            return
        rel = str(path.relative_to(job_dir.resolve()))
        if any(item["rel"] == rel for item in job.files):
            return
        entry = {
            "id": uuid.uuid4().hex[:12],
            "index": event.get("index"),
            "name": path.name,
            "rel": rel,
            "size": path.stat().st_size,
            "title": (str(event["title"])[:300] if event.get("title") else None),
            "artist": (str(event["artist"])[:300] if event.get("artist") else None),
            "delivered": False,
        }
        if self.settings.deliver:
            try:
                self._deliver(path)
                entry["delivered"] = True
            except OSError as exc:
                log.error("job %s: could not copy %s to %s: %s", job.id, path.name, self.settings.output_dir, exc)
                job.errors.append({
                    "index": event.get("index"),
                    "name": path.name,
                    "message": "Downloaded, but it couldn't be copied to the output folder. Download it from here instead.",
                })
        job.files.append(entry)

    def _deliver(self, path: Path) -> None:
        """Copies a song into the output folder under a name nothing else has.
        It is written as a dot file and renamed when complete: musicguard
        skips dot files, so it never sees half a song."""
        inbox = self.settings.output_dir
        with self._deliver_lock:
            inbox.mkdir(parents=True, exist_ok=True)
            stem, ext = os.path.splitext(path.name)
            target = inbox / path.name
            number = 2
            while target.exists():
                target = inbox / f"{stem} ({number}){ext}"
                number += 1
            partial = inbox / f".{target.name}.songdrop"
            try:
                shutil.copyfile(path, partial)
                os.chmod(partial, 0o644)
                os.replace(partial, target)
            except OSError:
                partial.unlink(missing_ok=True)
                raise

    def _finish(self, job: Job, forced: str | None = None, message: str | None = None) -> None:
        if forced:
            status = forced if not (forced == "failed" and job.files) else "partial"
        elif job.files and job.errors:
            status = "partial"
        elif job.files:
            status = "done"
        else:
            status = "failed"
            message = message or ("Nothing could be downloaded." if job.errors else "Nothing was downloaded.")
        if forced == "cancelled":
            status = "cancelled"
        if status == "failed" and message:
            # A single link that fails says why once, not again as a song.
            job.errors = [item for item in job.errors if item["message"] != message]
        job.status = status
        job.message = message
        job.finished_at = time.time()
        job.current = {}
        self._tidy(job)
        self._save(job)

    def _tidy(self, job: Job) -> None:
        """Leaves only job.json and the songs: no thumbnails, partial
        downloads or engine caches."""
        job_dir = self._dir(job.id)
        keep = {item["rel"] for item in job.files} | {JOB_FILE}
        for scratch in SCRATCH:
            shutil.rmtree(job_dir / scratch, ignore_errors=True)
        for entry in job_dir.iterdir() if job_dir.is_dir() else []:
            if entry.name in keep or entry.name == ARCHIVE_DIR:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)

    # ---- processes -------------------------------------------------------

    def _signal(self, process: subprocess.Popen, signum: int) -> None:
        try:
            os.killpg(process.pid, signum)
        except (ProcessLookupError, PermissionError):
            pass

    def _stop_process(self, process: subprocess.Popen) -> None:
        self._signal(process, signal.SIGTERM)

        def kill_if_alive() -> None:
            if process.poll() is None:
                self._signal(process, signal.SIGKILL)

        timer = threading.Timer(KILL_GRACE_SECONDS, kill_if_alive)
        timer.daemon = True
        timer.start()

    # ---- housekeeping ----------------------------------------------------

    def _janitor(self) -> None:
        while not self._stopping.wait(JANITOR_INTERVAL_SECONDS):
            try:
                self.expire()
            except Exception:
                log.exception("cleanup failed")

    def expire(self, now: float | None = None) -> int:
        """Removes finished jobs older than the retention, and folders that
        belong to no job."""
        now = time.time() if now is None else now
        limit = self.settings.retention_hours * 3600
        removed = []
        with self._lock:
            for job in list(self._jobs.values()):
                waiting = any(entry.get("state") in PENDING for entry in job.errors)
                if job.status not in ACTIVE and not waiting and job.finished_at and now - job.finished_at > limit:
                    del self._jobs[job.id]
                    removed.append(job.id)
            known = set(self._jobs)
        for job_id in removed:
            shutil.rmtree(self._dir(job_id), ignore_errors=True)
        for folder in self.settings.jobs_dir.iterdir():
            if folder.is_dir() and folder.name not in known and now - folder.stat().st_mtime > limit:
                shutil.rmtree(folder, ignore_errors=True)
        if removed:
            log.info("removed %d expired job(s)", len(removed))
        return len(removed)

    # ---- helpers ---------------------------------------------------------

    def _dir(self, job_id: str) -> Path:
        return self.settings.jobs_dir / job_id

    def _inside(self, job_id: str, rel: str) -> Path | None:
        base = self._dir(job_id).resolve()
        path = (base / rel).resolve()
        return path if base in path.parents else None

    def _find(self, job_id: str) -> Job:
        job = self._jobs.get(job_id) if JOB_ID.match(job_id or "") else None
        if job is None:
            raise JobError(404, "No such download.")
        return job

    def _save(self, job: Job) -> None:
        path = self._dir(job.id) / JOB_FILE
        if not path.parent.is_dir():
            return
        partial = path.with_suffix(".json.part")
        partial.write_text(json.dumps(job.saved(), ensure_ascii=False), encoding="utf-8")
        os.replace(partial, path)

    def _public(self, job: Job) -> dict:
        finished = len(job.files) + len({item["index"] for item in job.errors if item.get("index") is not None})
        running = sum(item["percent"] for item in job.current.values()) / 100
        progress = None
        if job.total:
            progress = min(1.0, (finished + running) / job.total)
        current = next(iter(job.current.values()), None)
        expires_at = None
        if job.finished_at and job.status not in ACTIVE:
            expires_at = job.finished_at + self.settings.retention_hours * 3600
        return {
            "id": job.id,
            "url": job.url,
            "engine": job.engine,
            "site": job.site,
            "kind": job.kind,
            "status": job.status,
            "title": job.title,
            "message": job.message,
            "total": job.total,
            "completed": len(job.files),
            "progress": progress,
            "current": current["name"] if current else None,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "expires_at": expires_at,
            "files": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "size": item["size"],
                    "title": item["title"],
                    "artist": item["artist"],
                    "delivered": item["delivered"],
                    "url": f"/api/v1/jobs/{job.id}/files/{item['id']}",
                }
                for item in sorted(job.files, key=lambda entry: (entry["index"] is None, entry["index"] or 0))
            ],
            "errors": [
                {
                    "index": item.get("index"),
                    "name": item["name"],
                    "message": item["message"],
                    "url": item.get("url"),
                    "state": item.get("state", "failed"),
                    "tries": item.get("tries", 1),
                    "max_tries": self.max_tries,
                    "next_retry_at": item.get("next_retry_at"),
                }
                for item in job.errors
            ],
            "notes": [dict(item) for item in job.notes],
            "archive_url": f"/api/v1/jobs/{job.id}/archive" if len(job.files) > 1 and job.status not in ACTIVE else None,
        }


def _minutes(seconds: float) -> str:
    seconds = max(0, round(seconds))
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min"
    hours = seconds / 3600
    return f"{hours:.0f} h" if hours >= 2 else f"{hours:.1f} h"
