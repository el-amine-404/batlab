"""A download's log: what happened to it, line by line, for whoever has to
find out why it went as it did. Who asked, each try, what yt-dlp said, the
fingerprint and whether a file was reused, each step and how long it took,
where the files went and their checks, each callback, the held files
removed.

Kept in the database with the job (forgotten with it), MAX_LINES a job at
most (the newest). Every line goes through redact.clean: no secret, no
forged line."""

import time
from typing import Literal

from .db import Database
from .redact import clean

Level = Literal["debug", "info", "warning", "error"]
LEVELS: tuple[Level, ...] = ("debug", "info", "warning", "error")
# Who said it: videodrop itself, yt-dlp (and the ffmpeg it runs), the
# download's process (what it printed besides), a callback.
Source = Literal["videodrop", "yt-dlp", "process", "callback"]
MAX_LINES = 3000
# Lines answered at most at once.
PAGE = 1000


class Journal:
    def __init__(self, db: Database):
        self._db = db

    def add(self, job_id: str, message: str, level: Level = "info", source: Source = "videodrop") -> None:
        self._db.run("INSERT INTO job_log (job_id, at, level, source, message) VALUES (?, ?, ?, ?, ?)",
                     (job_id, time.time(), level if level in LEVELS else "info", source, clean(message)))

    def lines(self, job_id: str, after: int = 0, limit: int = PAGE) -> list[dict]:
        """Its lines after the one numbered after (0: from the first), oldest first."""
        rows = self._db.all("SELECT seq, at, level, source, message FROM job_log WHERE job_id = ? AND seq > ? "
                            "ORDER BY seq LIMIT ?", (job_id, after, min(limit, PAGE)))
        return [dict(row) for row in rows]

    def trim(self, job_id: str) -> None:
        """Only its newest MAX_LINES (after each run)."""
        self._db.run("DELETE FROM job_log WHERE job_id = ? AND seq <= (SELECT seq FROM job_log WHERE job_id = ? "
                     "ORDER BY seq DESC LIMIT 1 OFFSET ?)", (job_id, job_id, MAX_LINES))

    def text(self, job_id: str) -> str:
        """All of it as a text file: a line each, its time in UTC."""
        out, after = [], 0
        while batch := self.lines(job_id, after):
            for line in batch:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(line["at"]))
                out.append(f"{stamp}.{int(line['at'] % 1 * 1000):03d}Z {line['level'].upper():7} "
                           f"{line['source']:9} {line['message']}")
            after = batch[-1]["seq"]
        return "\n".join(out) + ("\n" if out else "")
