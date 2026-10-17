"""Requests, as the database keeps them (the requests table). A request is
one song (audio) or one video wanted, with what it is (media: a snapshot, so
it reads the same when the catalog changes) and every attempt a service made
at it."""

from __future__ import annotations

import json
import time
import uuid
from typing import Literal

from ..db import Database

Status = Literal["queued", "sent", "downloading", "completed", "failed", "cancelled"]
ACTIVE: tuple[Status, ...] = ("queued", "sent", "downloading")
DONE: tuple[Status, ...] = ("completed",)
FAILED: tuple[Status, ...] = ("failed", "cancelled")
FILTERS = {"active": ACTIVE, "completed": DONE, "failed": FAILED}


class NotFound(LookupError):
    """The message is shown to the user as is."""


def _row(row) -> dict:
    payload = json.loads(row["payload"])
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "media": payload["media"],
        "round": payload.get("round", 0),
        "attempts": json.loads(row["attempts"]),
        "message": row["message"],
        "quality": row["quality"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "finished_at": row["finished_at"],
    }


class Requests:
    def __init__(self, db: Database):
        self._db = db

    def create(self, kind: str, media: dict) -> dict:
        now = time.time()
        request_id = uuid.uuid4().hex[:16]
        self._db.run("INSERT INTO requests (id, kind, status, video_key, payload, attempts, message, created_at, "
                     "updated_at) VALUES (?, ?, 'queued', ?, ?, '[]', ?, ?, ?)",
                     (request_id, kind, media["video_key"], json.dumps({"media": media, "round": 0}),
                      "Waiting for a service.", now, now))
        return self.get(request_id)

    def get(self, request_id: str) -> dict:
        row = self._db.one("SELECT * FROM requests WHERE id = ?", (request_id,))
        if not row:
            raise NotFound("No such request.")
        return _row(row)

    def save(self, request: dict) -> dict:
        request["updated_at"] = time.time()
        if request["status"] not in ACTIVE and not request.get("finished_at"):
            request["finished_at"] = request["updated_at"]
        if request["status"] in ACTIVE:
            request["finished_at"] = None
        self._db.run("UPDATE requests SET status = ?, payload = ?, attempts = ?, message = ?, quality = ?, "
                     "updated_at = ?, finished_at = ? WHERE id = ?",
                     (request["status"], json.dumps({"media": request["media"], "round": request["round"]}),
                      json.dumps(request["attempts"]), request["message"], request["quality"],
                      request["updated_at"], request["finished_at"], request["id"]))
        return request

    def delete(self, request_id: str) -> None:
        if not self._db.run("DELETE FROM requests WHERE id = ?", (request_id,)):
            raise NotFound("No such request.")

    def active(self) -> list[dict]:
        marks = ",".join("?" * len(ACTIVE))
        return [_row(r) for r in self._db.all(f"SELECT * FROM requests WHERE status IN ({marks}) "
                                              "ORDER BY created_at", ACTIVE)]

    def open_for(self, video_key: str, kind: str) -> dict | None:
        """The request for this video and kind that is under way or done, if any."""
        row = self._db.one("SELECT * FROM requests WHERE video_key = ? AND kind = ? AND status NOT IN "
                           "('failed', 'cancelled') ORDER BY created_at DESC LIMIT 1", (video_key, kind))
        return _row(row) if row else None

    def for_videos(self, video_keys: list[str]) -> list[dict]:
        if not video_keys:
            return []
        marks = ",".join("?" * len(video_keys))
        return [_row(r) for r in self._db.all(f"SELECT * FROM requests WHERE video_key IN ({marks}) "
                                              "ORDER BY created_at DESC", tuple(video_keys))]

    def list(self, status: str = "all", limit: int = 50, offset: int = 0, query: str = "") -> tuple[list[dict], int]:
        where, params = [], []
        if status in FILTERS:
            where.append(f"status IN ({','.join('?' * len(FILTERS[status]))})")
            params.extend(FILTERS[status])
        if query:
            where.append("payload LIKE ? ESCAPE '\\'")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self._db.one(f"SELECT COUNT(*) AS n FROM requests {clause}", tuple(params))["n"]
        rows = self._db.all(f"SELECT * FROM requests {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                            (*params, limit, offset))
        return [_row(r) for r in rows], total

    def counts(self) -> dict[str, int]:
        rows = self._db.all("SELECT status, COUNT(*) AS n FROM requests GROUP BY status")
        by_status = {r["status"]: r["n"] for r in rows}
        counts = {name: sum(by_status.get(s, 0) for s in statuses) for name, statuses in FILTERS.items()}
        counts["all"] = sum(by_status.values())
        return counts
