"""The downloads (jobs): what each one was asked for, where it is, and the
files it left. The store only keeps them; the scheduler (scheduler.py) runs
them and tells the store as they go.

A job asked for while the same one (same link, destination and options) is
still queued or running is that job: callers can ask again safely."""

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import links, redact
from .db import Database
from .domain import ACTIVE, FINAL, Conflict, Gone, Invalid, NotFound, Status
from .options import OptionsPatch, Section, VideoOptions

CALLBACK_EVENTS = ("started", "progress", "finished", "failed", "cancelled")
DEFAULT_CALLBACK_EVENTS = ("started", "finished", "failed", "cancelled")
# Jobs listed in one answer at most.
MAX_PAGE = 200


class Song(BaseModel):
    """The song a music video carries, as its caller knows it."""

    model_config = ConfigDict(extra="forbid")

    artist: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)


class JobIn(BaseModel):
    """A download to make. Only the link is needed."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2000, description="The video (or playlist) on its site.",
                     examples=["https://www.youtube.com/watch?v=DPEodkHzH-s"])
    destination: str | None = Field(None, max_length=40,
                                    description="Where its files go (GET /api/v1/destinations). Left out: the "
                                                "settings' default destination; when there is none, the files are "
                                                "kept for the caller to fetch (hold_hours).",
                                    examples=["music-videos"])
    options: OptionsPatch = Field(default_factory=OptionsPatch,
                                  description="Options of its own; the destination's, then the settings', fill in "
                                              "the rest.")
    section: Section | None = Field(None, description="Only this part of the video, cut at the keyframes "
                                                      "around it (no re-encoding: it may start a few seconds "
                                                      "early). Its times end the file's name. Not for a "
                                                      "playlist.")
    hold_hours: int | None = Field(None, ge=1, le=24 * 30,
                                   description="Without a destination: how long its files are kept for the caller "
                                               "after the download, in hours (each file's expires_at). Left out: "
                                               "the settings' hold_hours. A file asked for after that answers 410 "
                                               "Gone; POST /jobs/{id}/retry downloads it again, same job.")
    priority: int = Field(0, ge=-10, le=10, description="Higher goes first among queued jobs; then oldest first.")
    again: bool = Field(False, description="Download it even when an earlier download got the same file: the site "
                                           "serves the same video (its fingerprint), with the same options, to the "
                                           "same place, and that file is still there. Left false, that file is "
                                           "reused and nothing is downloaded.")
    song: Song | None = Field(None, description="The song, when it is a music video and the caller knows it: what "
                                                "its lyrics are looked up by (options.lyrics). Left out: guessed "
                                                "from the video's title.")
    client: str | None = Field(None, max_length=40, pattern=r"^[\w .-]+$",
                               description="Who asks (an app's name), shown on the page and filterable.",
                               examples=["clipseerr"])
    callback_url: str | None = Field(None, max_length=500,
                                     description="POSTed {event, time, job} as the job goes (callback_events). "
                                                 "Tried again for a while when it fails.",
                                     examples=["http://clipseerr:8820/api/v1/hooks/videodrop"])
    callback_secret: str | None = Field(None, min_length=16, max_length=200,
                                        description="Signs each callback: X-Videodrop-Signature: sha256=<HMAC-SHA256 "
                                                    "of \"<X-Videodrop-Timestamp>.<body>\">. Never answered back.")
    callback_events: list[str] | None = Field(None, description="Of started, progress (every 10 s at most), "
                                                                "finished, failed, cancelled. Left out: all but "
                                                                "progress.")

    @field_validator("callback_url")
    @classmethod
    def _callback_url(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            raise ValueError("The callback address starts with http:// or https://.")
        return value

    @field_validator("callback_events")
    @classmethod
    def _callback_events(cls, value):
        if value is not None and any(name not in CALLBACK_EVENTS for name in value):
            raise ValueError(f"Callback events are among {', '.join(CALLBACK_EVENTS)}.")
        return value


@dataclass(frozen=True)
class JobQuery:
    status: tuple[Status, ...] = ()
    destination: str | None = None
    client: str | None = None
    q: str | None = None
    limit: int = 50
    offset: int = 0

    def where(self) -> tuple[str, tuple]:
        clauses, params = [], []
        if self.status:
            clauses.append(f"status IN ({','.join('?' * len(self.status))})")
            params += self.status
        if self.destination:
            clauses.append("destination = ?")
            params.append(self.destination)
        if self.client:
            clauses.append("client = ?")
            params.append(self.client)
        if self.q:
            clauses.append("(title LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\' OR uploader LIKE ? ESCAPE '\\')")
            like = "%" + self.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params += [like, like, like]
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", tuple(params)


def section_text(section: Section | None) -> str | None:
    """A section as the jobs table keeps it (compared as text)."""
    return json.dumps([section.start, section.end]) if section else None


def dedupe_key(url: str, destination: str | None, options: VideoOptions, section: Section | None = None) -> str:
    raw = json.dumps([links.canonical(url), destination or "", options.model_dump(), section_text(section)],
                     sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class JobStore:
    def __init__(self, db: Database, held_dir: Path, media_root: Path):
        self._db = db
        self._held = held_dir
        self._root = media_root
        # The scheduler's live progress of a running job, else None.
        self._live: Callable[[str], dict | None] = lambda job_id: None
        self._changed: list[Callable[[str], None]] = []
        # The settings' hold_hours: how long held files are kept when the job didn't say.
        self._hold_hours: Callable[[], int] = lambda: 24

    def hold_from(self, hours: Callable[[], int]) -> None:
        self._hold_hours = hours

    def hold_hours(self, row: sqlite3.Row) -> int:
        return row["hold_hours"] or self._hold_hours()

    def expires_at(self, row: sqlite3.Row) -> float | None:
        """When its held files go (none before it has finished)."""
        return row["finished_at"] + self.hold_hours(row) * 3600 if row["finished_at"] else None

    def live_from(self, live: Callable[[str], dict | None]) -> None:
        self._live = live

    def on_change(self, listener: Callable[[str], None]) -> None:
        """listener(job_id) after any change of a job (its stream, the scheduler)."""
        self._changed.append(listener)

    def _touch(self, job_id: str) -> None:
        for listener in self._changed:
            listener(job_id)

    # ---- reading -------------------------------------------------------------------------

    def row(self, job_id: str) -> sqlite3.Row:
        found = self._db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if not found:
            raise NotFound(f"There's no download {job_id!r}.")
        return found

    def files(self, job_id: str) -> list[sqlite3.Row]:
        return self._db.all("SELECT * FROM files WHERE job_id = ? ORDER BY position", (job_id,))

    def file_path(self, row: sqlite3.Row) -> Path:
        base = self._held if row["root"] == "held" else self._root
        return base / row["path"]

    def available(self, job_id: str, index: int, base_url: str = "") -> tuple[sqlite3.Row, Path]:
        """A job's file n, still there. Raises NotFound when there is no such
        file, Gone when there was (removed after its hold, or moved away)."""
        row = self.row(job_id)
        file = self._db.one("SELECT * FROM files WHERE job_id = ? AND position = ?", (job_id, index))
        if not file:
            raise NotFound(f"Download {job_id!r} has no file {index}.")
        path = self.file_path(file)
        again = f"{base_url.rstrip('/')}/api/v1/jobs/{job_id}/retry"
        if file["removed_at"] is not None:
            when = time.strftime("%d %b %Y at %H:%M UTC", time.gmtime(file["removed_at"]))
            raise Gone(f"This file was kept {self.hold_hours(row)} h after its download, then removed on {when}. "
                       f"To have it again, POST {again} (the same job downloads it again), or ask for a new "
                       "download.", again, file["removed_at"])
        if not path.is_file():
            raise Gone("This file isn't in its folder any more: it was moved or deleted there. To have it again, "
                       f"POST {again}.", again)
        return file, path

    def every_file(self) -> list[Path]:
        """Every file a job has, still there or not."""
        return [self.file_path(row) for row in self._db.all("SELECT * FROM files WHERE removed_at IS NULL")]

    def public(self, row: sqlite3.Row, base_url: str | None = None) -> dict:
        """The job as the API answers it. base_url: how the caller reaches
        videodrop, for its files' download links; the address the job was
        asked through when none."""
        base = (base_url or row["base_url"] or "").rstrip("/")
        expires = self.expires_at(row)
        files = []
        for file in self.files(row["id"]):
            available = file["removed_at"] is None and self.file_path(file).is_file()
            path = f"/api/v1/jobs/{row['id']}/files/{file['position']}"
            files.append({"index": file["position"], "name": file["name"], "kind": file["kind"],
                          "size": file["size"], "path": file["path"] if file["root"] == "destination" else None,
                          "existed": bool(file["existed"]), "available": available,
                          "expires_at": expires if file["root"] == "held" and available else None,
                          "removed_at": file["removed_at"],
                          "checks": json.loads(file["checks"]) if file["checks"] else None,
                          "download_url": f"{base}{path}" if available else None})
        progress = self._live(row["id"]) if row["status"] == "running" else None
        if progress is None and row["status"] == "running":
            progress = json.loads(row["progress"]) or None
        callback = None
        if row["callback_url"]:
            # Its address as given, but a password in it is the caller's and isn't answered back.
            callback = {"url": redact.secrets(row["callback_url"]), "events": json.loads(row["callback_events"]),
                        "signed": bool(row["callback_secret"]),
                        "last": json.loads(row["callback_last"]) if row["callback_last"] else None}
        return {
            "id": row["id"],
            "url": row["url"],
            "site": row["site"],
            "status": row["status"],
            "stage": ((progress or {}).get("stage") or row["stage"]) if row["status"] == "running" else None,
            "destination": row["destination"],
            "folder": row["folder"],
            "client": row["client"],
            "priority": row["priority"],
            "title": row["title"],
            "uploader": row["uploader"],
            "thumbnail": row["thumbnail"],
            "duration": row["duration"],
            "options": json.loads(row["options"]),
            "progress": progress,
            "error": row["error"],
            "notes": json.loads(row["notes"] or "[]"),
            "fingerprint": row["fingerprint"],
            "reused_from": row["reused_from"],
            "song": json.loads(row["song"]) if row["song"] else None,
            "section": dict(zip(("start", "end"), json.loads(row["section"]))) if row["section"] else None,
            "attempts": row["attempts"],
            "retry_at": row["retry_at"],
            "hold_hours": self.hold_hours(row),
            "files": files,
            "callback": callback,
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "updated_at": row["updated_at"],
            "links": {"self": f"{base}/api/v1/jobs/{row['id']}", "events": f"{base}/api/v1/jobs/{row['id']}/events",
                      "log": f"{base}/api/v1/jobs/{row['id']}/log"},
        }

    def query(self, query: JobQuery) -> tuple[list[sqlite3.Row], int]:
        where, params = query.where()
        total = self._db.one(f"SELECT COUNT(*) AS n FROM jobs{where}", params)["n"]
        rows = self._db.all(f"SELECT * FROM jobs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                            (*params, min(query.limit, MAX_PAGE), query.offset))
        return rows, total

    def counts(self) -> dict[str, int]:
        found = {row["status"]: row["n"] for row in self._db.all("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
        return {status: found.get(status, 0) for status in ("queued", "running", "finished", "failed", "cancelled")}

    def clients(self) -> list[str]:
        return [row["client"] for row in self._db.all("SELECT DISTINCT client FROM jobs WHERE client IS NOT NULL "
                                                      "ORDER BY client")]

    # ---- what the API changes ---------------------------------------------------------------

    def create(self, body: JobIn, *, destination: sqlite3.Row | None, options: VideoOptions, template: str,
               base_url: str | None) -> tuple[sqlite3.Row, bool]:
        """The new job, queued, and True; or the same job still queued or
        running, and False."""
        url = links.check(body.url)
        slug = destination["slug"] if destination else None
        if body.section and options.playlist:
            raise Invalid("A section is a part of one video, not of a playlist: turn the playlist option off.")
        key = dedupe_key(url, slug, options, body.section)
        same = self._db.one(f"SELECT * FROM jobs WHERE dedupe_key = ? AND status IN ({','.join('?' * len(ACTIVE))}) "
                            "ORDER BY created_at LIMIT 1", (key, *ACTIVE))
        if same:
            return same, False
        job_id = uuid.uuid4().hex
        now = time.time()
        events = body.callback_events if body.callback_events is not None else list(DEFAULT_CALLBACK_EVENTS)
        self._db.run(
            "INSERT INTO jobs (id, url, site, dedupe_key, destination, folder, template, options, priority, client, "
            "status, callback_url, callback_secret, callback_events, base_url, song, again, section, hold_hours, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, url, links.site_of(url), key, slug, destination["folder"] if destination else None, template,
             json.dumps(options.model_dump()), body.priority, body.client, body.callback_url, body.callback_secret,
             json.dumps([e for e in CALLBACK_EVENTS if e in events]), base_url,
             body.song.model_dump_json() if body.song else None,
             int(body.again), section_text(body.section), body.hold_hours, now, now))
        self._touch(job_id)
        return self.row(job_id), True

    def retry(self, job_id: str) -> sqlite3.Row:
        """A failed or cancelled job, or a finished one whose files are all
        gone (removed after their hold, moved away), queued again from the
        start: the same job, the same links."""
        row = self.row(job_id)
        if row["status"] == "finished":
            files = self.files(job_id)
            if any(f["removed_at"] is None and self.file_path(f).is_file() for f in files):
                raise Conflict("Its files are still there: download them, or ask for a new download.")
        elif row["status"] not in ("failed", "cancelled"):
            raise Conflict("Only an ended download can be tried again.")

        def work(conn):
            conn.execute("DELETE FROM files WHERE job_id = ?", (job_id,))
            conn.execute("UPDATE jobs SET status = 'queued', stage = NULL, error = NULL, attempts = 0, "
                         "retry_at = NULL, progress = '{}', notes = '[]', fingerprint = NULL, reused_from = NULL, "
                         "started_at = NULL, finished_at = NULL, updated_at = ? WHERE id = ?", (time.time(), job_id))

        self._db.transaction(work)
        self._touch(job_id)
        return self.row(job_id)

    def delete(self, job_id: str) -> None:
        """Forgets an ended job (its files are the caller's to remove)."""
        if self.row(job_id)["status"] not in FINAL:
            raise Conflict("Cancel the download first.")
        self._db.run("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._touch(job_id)

    # ---- what the scheduler changes ------------------------------------------------------------

    def next_queued(self, now: float, skip_sites: set[str], limit: int) -> list[sqlite3.Row]:
        """Queued jobs due now, the most pressing first, of sites not at their limit."""
        rows = self._db.all("SELECT * FROM jobs WHERE status = 'queued' AND (retry_at IS NULL OR retry_at <= ?) "
                            "ORDER BY priority DESC, created_at LIMIT ?", (now, limit + 50))
        return [row for row in rows if row["site"] not in skip_sites][:limit]

    def next_retry(self) -> float | None:
        row = self._db.one("SELECT MIN(retry_at) AS at FROM jobs WHERE status = 'queued' AND retry_at IS NOT NULL")
        return row["at"] if row else None

    def update(self, job_id: str, **fields) -> None:
        if "progress" in fields:
            fields["progress"] = json.dumps(fields["progress"] or {})
        for column in ("callback_last", "notes"):
            if column in fields:
                fields[column] = json.dumps(fields[column])
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in fields)
        self._db.run(f"UPDATE jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))
        self._touch(job_id)

    def add_files(self, job_id: str, files: list[dict]) -> None:
        def work(conn):
            start = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM files WHERE job_id = ?",
                                 (job_id,)).fetchone()[0]
            conn.executemany("INSERT INTO files (job_id, position, root, path, name, size, kind, existed, checks) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             [(job_id, start + n, f["root"], f["path"], f["name"], f["size"], f["kind"],
                               int(f["existed"]), json.dumps(f["checks"]) if f.get("checks") else None)
                              for n, f in enumerate(files)])

        self._db.transaction(work)

    def reusable(self, fingerprint: str, options: str, folder: str | None, template: str,
                 section: str | None = None, exclude: str | None = None) -> sqlite3.Row | None:
        """The newest finished job that got the same file: the same
        fingerprint, options, section and place, every file still there."""
        rows = self._db.all("SELECT * FROM jobs WHERE fingerprint = ? AND status = 'finished' AND options = ? AND "
                            "folder IS ? AND template = ? AND section IS ? AND id IS NOT ? "
                            "ORDER BY finished_at DESC LIMIT 5",
                            (fingerprint, options, folder, template, section, exclude))
        for row in rows:
            files = self.files(row["id"])
            if files and all(f["removed_at"] is None and self.file_path(f).is_file() for f in files):
                return row
        return None

    def requeue_running(self) -> int:
        """After a restart: what was running starts again (the try isn't counted)."""
        return self._db.run("UPDATE jobs SET status = 'queued', stage = NULL, attempts = MAX(attempts - 1, 0), "
                            "progress = '{}', notes = '[]', updated_at = ? WHERE status = 'running'", (time.time(),))

    def expired_held(self, now: float, forget_before: float | None = None) -> list[sqlite3.Row]:
        """Held files past their job's hold (or of a job about to be forgotten)."""
        return self._db.all("SELECT files.*, jobs.hold_hours AS job_hold FROM files JOIN jobs ON jobs.id = files.job_id "
                            "WHERE files.root = 'held' AND files.removed_at IS NULL AND jobs.finished_at IS NOT NULL "
                            "AND (jobs.finished_at + COALESCE(jobs.hold_hours, ?) * 3600 < ? OR jobs.finished_at < ?)",
                            (self._hold_hours(), now, forget_before or 0))

    def file_removed(self, job_id: str, position: int) -> None:
        self._db.run("UPDATE files SET removed_at = ? WHERE job_id = ? AND position = ?",
                     (time.time(), job_id, position))

    def forget(self, before: float) -> int:
        """Ended jobs older than before; their held files are gone by then."""
        return self._db.run(f"DELETE FROM jobs WHERE status IN ({','.join('?' * len(FINAL))}) AND finished_at < ?",
                            (*FINAL, before))

