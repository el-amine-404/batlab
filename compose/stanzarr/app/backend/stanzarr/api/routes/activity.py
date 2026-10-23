"""What stanzarr is doing now, and what it did: who asked for what, what
was written, what failed and why (Activity)."""

import time

from fastapi import APIRouter, Query

from ..deps import Ctx

router = APIRouter(prefix="/activity", tags=["System"])

KINDS = ("lyrics", "search", "filter", "ai", "scan", "workers")


@router.get("/now", summary="What stanzarr is doing now: the scan or the search and how far, the AI jobs")
def now(ctx: Ctx):
    background = ctx.background
    jobs = ctx.db.all("SELECT j.id, j.song_id, j.status, j.stage, j.worker, j.created_at, j.started_at, s.artist, s.title"
                      " FROM jobs j JOIN songs s ON s.id=j.song_id WHERE j.status IN ('running','queued')"
                      " ORDER BY j.status='running' DESC, j.id")
    return {"busy": background.busy if background else "", "progress": background.progress if background else {},
            "last_scan": background.last_scan if background else {}, "jobs": jobs,
            "workers_online": [{"name": w["name"], "gpu": w["gpu"]} for w in ctx.workers_online()],
            "at": time.time()}


@router.get("", summary="What stanzarr did, newest first: who asked, what happened, what failed")
def log(ctx: Ctx, kind: str = Query("", max_length=20, description=f"One of {', '.join(KINDS)}."),
        ok: bool | None = Query(None, description="false: only what failed."),
        q: str = Query("", max_length=200, description="In what happened and who asked."),
        song_id: int | None = Query(None, ge=1, le=2**62),
        page: int = Query(1, ge=1, le=100_000), size: int = Query(50, ge=1, le=200)):
    where, args = [], []
    if kind:
        where.append("e.kind = ?")
        args.append(kind)
    if ok is not None:
        where.append("e.ok = ?")
        args.append(int(ok))
    if q.strip():
        where.append("(e.message LIKE ? OR e.actor LIKE ? OR e.detail LIKE ?)")
        args += [f"%{q.strip()}%"] * 3
    if song_id:
        where.append("e.song_id = ?")
        args.append(song_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = ctx.db.one(f"SELECT COUNT(*) AS n FROM events e {clause}", args)["n"]
    rows = ctx.db.all(f"SELECT e.*, s.artist, s.title FROM events e LEFT JOIN songs s ON s.id=e.song_id {clause}"
                      " ORDER BY e.id DESC LIMIT ? OFFSET ?", args + [size, (page - 1) * size])
    for row in rows:
        row["ok"] = bool(row["ok"])
    failed = ctx.db.one("SELECT COUNT(*) AS n FROM events WHERE ok = 0 AND at > ?", (time.time() - 86400,))["n"]
    return {"total": total, "page": page, "size": size, "failed_today": failed, "events": rows}
