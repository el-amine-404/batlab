"""The AI queue and the GPU workers: the jobs as people see them, the
workers' key, and the protocol a worker speaks (stanzarr-worker): check in,
take the next job, fetch its song, report stages, send the voice and the
result. A worker authenticates with the workers' key (X-Stanzarr-Key), not
an account; the protocol answers at /api/v1/worker/… and, for the workers
before 1.0, /api/worker/…."""

import os
import time

from batlab_common.accounts import Unauthorized
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

from ... import operations
from ...domain import Conflict, Invalid, NotFound
from ...events import Event
from .. import schemas
from ..auth import Who, need
from ..deps import Ctx
from .songs import song_or_404

MAX_UPLOAD = 80 * 1024 * 1024

router = APIRouter(tags=["AI sync"])
protocol = APIRouter(prefix="/worker", tags=["Workers' protocol"])


# ---- the queue ----------------------------------------------------------------------------

@router.get("/jobs", summary="The AI jobs: running, queued, then the latest done", response_model=list[schemas.Job])
def jobs(ctx: Ctx):
    return ctx.db.all("SELECT j.*, s.artist, s.title FROM jobs j JOIN songs s ON s.id=j.song_id ORDER BY CASE"
                      " j.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, j.id DESC LIMIT 200")


@router.delete("/jobs/{job_id}", summary="Cancel a queued or running job", response_model=schemas.Ok,
               dependencies=[need("ai")])
def cancel(job_id: int, ctx: Ctx, who: Who):
    if not ctx.db.run("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status IN ('queued','running')",
                      (int(time.time()), job_id)):
        raise NotFound("There's no queued or running job with that id.")
    job = ctx.db.one("SELECT song_id FROM jobs WHERE id=?", (job_id,))
    ctx.db.event("ai", "AI sync cancelled", job["song_id"] if job else None, who.name)
    return {"ok": True}


@router.post("/jobs/{job_id}/retry", summary="Queue a failed or cancelled job again", response_model=schemas.Ok,
             dependencies=[need("ai")])
def retry(job_id: int, ctx: Ctx):
    if not ctx.db.run("UPDATE jobs SET status='queued', worker='', stage='', error='' WHERE id=? AND status IN"
                      " ('failed','cancelled')", (job_id,)):
        raise NotFound("There's no failed or cancelled job with that id.")
    return {"ok": True}


class WorkerIn(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="How it shows here and in the queue: laptop, desktop…")


def _workers(ctx) -> list[dict]:
    """Each worker with a key, and what it reported last; the ones on the shared key too."""
    seen = {r["name"]: r for r in ctx.db.all("SELECT * FROM workers")}
    now = int(time.time())
    out = []
    for key in ctx.worker_keys():
        report = seen.pop(key["name"], None) or {}
        out.append({**key, "shared_key": False, "gpu": report.get("gpu", ""), "vram": report.get("vram", 0),
                    "version": report.get("version", ""), "last_seen": report.get("last_seen"),
                    "host": report.get("host", ""), "model": report.get("model", ""),
                    "capabilities": report.get("capabilities", "sync,generate"),
                    "online": bool(report) and now - report["last_seen"] < 60})
    for name, report in seen.items():
        out.append({"id": None, "name": name, "prefix": None, "created_at": None, "last_used": None, "shared_key": True,
                    "gpu": report["gpu"], "vram": report["vram"], "version": report["version"],
                    "last_seen": report["last_seen"], "host": report.get("host", ""), "model": report.get("model", ""),
                    "capabilities": report.get("capabilities", "sync,generate"),
                    "online": now - report["last_seen"] < 60})
    return out


@router.get("/workers", summary="The workers: their keys' names, and what each reported last",
            response_model=schemas.WorkerList)
def workers(ctx: Ctx):
    return {"workers": _workers(ctx), "shared_key": bool(ctx.legacy_key()), "shared_key_fixed": bool(ctx.config.worker_key)}


@router.post("/workers", status_code=201, summary="Add a worker: its key is answered this once",
             response_model=schemas.WorkerMade, dependencies=[need("settings")])
def add_worker(body: WorkerIn, ctx: Ctx, who: Who):
    key, row = ctx.add_worker(body.name)
    ctx.db.event("workers", f"Worker {row['name']} added", actor=who.name)
    return {"key": key, "worker": {**row, "shared_key": False, "gpu": "", "vram": 0, "version": "", "last_seen": None,
                                   "online": False, "host": "", "model": "", "capabilities": "sync,generate"}}


@router.patch("/workers/{worker_id}", summary="Rename a worker", response_model=schemas.Ok,
              dependencies=[need("settings")])
def rename_worker(worker_id: str, body: WorkerIn, ctx: Ctx, who: Who):
    row = ctx.rename_worker(worker_id, body.name)
    ctx.db.event("workers", f"Worker renamed {row['name']}", actor=who.name)
    return {"ok": True}


@router.delete("/workers/{worker_id}", status_code=204, summary="Remove a worker: its key stops working at once",
               dependencies=[need("settings")])
def remove_worker(worker_id: str, ctx: Ctx, who: Who):
    ctx.remove_worker(worker_id)
    ctx.db.event("workers", "A worker was removed: its key no longer works", actor=who.name)
    return Response(status_code=204)


@router.delete("/workers-shared-key", status_code=204, summary="Retire the key all workers shared before 1.1",
               dependencies=[need("settings")])
def forget_shared_key(ctx: Ctx, who: Who):
    ctx.forget_legacy_key()
    ctx.db.event("workers", "The workers' shared key was retired", actor=who.name)
    return Response(status_code=204)


# ---- the protocol -------------------------------------------------------------------------

def worker(request: Request, ctx: Ctx) -> str:
    """Which worker asks: by its own key, or (before 1.1) the shared key and the name it gives."""
    name = ctx.worker_for(request.headers.get("x-stanzarr-key", ""), request.headers.get("x-stanzarr-worker", ""))
    if not name:
        raise Unauthorized("Not a worker of this stanzarr: check its key (Settings, Workers).")
    return name


def _running(ctx, job_id: int, name: str) -> dict:
    job = ctx.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job or job["status"] != "running" or job["worker"] != name:
        raise Conflict("Not this worker's job, or no longer running.")
    return job


@protocol.post("/heartbeat", summary="A worker checks in (every 20 s)")
def heartbeat(request: Request, body: dict, ctx: Ctx, name: str = Depends(worker)):
    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    host = str(body.get("host") or client_ip)[:120]
    model = str(body.get("model") or "Demucs htdemucs + Whisper large-v3 + MMS")[:120]
    capabilities = str(body.get("capabilities") or "sync,generate")[:80]
    ctx.db.run("INSERT INTO workers(name, gpu, vram, version, last_seen, host, model, capabilities) VALUES(?, ?, ?, ?, ?, ?, ?, ?)"
               " ON CONFLICT(name) DO UPDATE SET gpu=excluded.gpu, vram=excluded.vram, version=excluded.version,"
               " last_seen=excluded.last_seen, host=excluded.host, model=excluded.model, capabilities=excluded.capabilities",
               (name, str(body.get("gpu", ""))[:120], int(body.get("vram", 0) or 0),
                str(body.get("version", ""))[:32], int(time.time()), host, model, capabilities))
    return {"ok": True}


@protocol.post("/next", summary="The next job, or 204 when there is none")
def next_job(ctx: Ctx, name: str = Depends(worker)):
    with ctx.db.tx() as conn:
        row = conn.execute("SELECT id, song_id, kind FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return Response(status_code=204)
        conn.execute("UPDATE jobs SET status='running', worker=?, started_at=?, stage='starting' WHERE id=?",
                     (name, int(time.time()), row["id"]))
    found = song_or_404(ctx, row["song_id"])
    lyrics = operations.best_text(ctx.db, row["song_id"])
    words = [line.s for line in (lyrics.lines if lyrics else []) if line.s.strip()
             and not (line.s.startswith("[") and line.s.endswith("]"))]
    kind = row["kind"] if "kind" in row.keys() else "ai_sync"
    return {"job": row["id"], "kind": kind, "song": {k: found[k] for k in ("artist", "title", "album", "duration")},
            "lines": words, "audio_name": os.path.basename(found["path"])}


@protocol.get("/jobs/{job_id}/audio", summary="The job's song", response_class=FileResponse)
def job_audio(job_id: int, ctx: Ctx, name: str = Depends(worker)):
    job = _running(ctx, job_id, name)
    return FileResponse(song_or_404(ctx, job["song_id"])["path"])


@protocol.post("/jobs/{job_id}/stage", summary="What the worker is doing now")
def job_stage(job_id: int, body: dict, ctx: Ctx, name: str = Depends(worker)):
    _running(ctx, job_id, name)
    ctx.db.run("UPDATE jobs SET stage=? WHERE id=?", (str(body.get("stage", ""))[:80], job_id))
    return {"ok": True}


@protocol.put("/jobs/{job_id}/voice", summary="The voice alone (Ogg Opus), for the editor")
async def job_voice(job_id: int, request: Request, ctx: Ctx, name: str = Depends(worker)):
    job = _running(ctx, job_id, name)
    if int(request.headers.get("content-length", "0") or 0) > MAX_UPLOAD:
        raise Invalid("The voice is over 80 MB.")
    directory = os.path.join(ctx.config.artifacts_dir, str(job["song_id"]))
    os.makedirs(directory, exist_ok=True)
    temporary = os.path.join(directory, ".vocals.ogg.tmp")
    size = 0
    with open(temporary, "wb") as out:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                os.unlink(temporary)
                raise Invalid("The voice is over 80 MB.")
            out.write(chunk)
    os.replace(temporary, os.path.join(directory, "vocals.ogg"))
    return {"ok": True}


@protocol.post("/jobs/{job_id}/result", summary="The synced lines")
def job_result(job_id: int, body: dict, ctx: Ctx, name: str = Depends(worker)):
    job = _running(ctx, job_id, name)
    lines = body.get("lines")
    if not isinstance(lines, list) or not lines:
        raise Invalid("Send the lines.")
    outcome = operations.save_ai_result(ctx.db, ctx.config, job["song_id"], lines, str(body.get("method", ""))[:200], name)
    ctx.db.run("UPDATE jobs SET status='finished', stage=?, finished_at=? WHERE id=?",
               (outcome[:80], int(time.time()), job_id))
    song = song_or_404(ctx, job["song_id"])
    unsure = sum(1 for line in lines if isinstance(line, dict) and line.get("u"))
    ctx.notifier.emit(Event("ai.synced", f"{song['artist']} – {song['title']}: synced by AI",
                            (f"{unsure} lines to check." if unsure else "Every line placed with confidence.",),
                            ctx.link(f"/songs/{song['id']}"),
                            {"song": {"id": song["id"], "artist": song["artist"], "title": song["title"]},
                             "unsure": unsure, "worker": name}))
    return {"ok": True, "result": outcome}


@protocol.post("/jobs/{job_id}/fail", summary="The job failed")
def job_fail(job_id: int, body: dict, ctx: Ctx, name: str = Depends(worker)):
    job = _running(ctx, job_id, name)
    error = str(body.get("error", "failed"))[:500]
    ctx.db.run("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?", (error, int(time.time()), job_id))
    ctx.db.event("ai", "AI sync failed", job["song_id"], name, ok=False, detail=error)
    song = song_or_404(ctx, job["song_id"])
    ctx.notifier.emit(Event("ai.failed", f"{song['artist']} – {song['title']}: AI sync failed", (error,),
                            ctx.link(f"/songs/{song['id']}"),
                            {"song": {"id": song["id"], "artist": song["artist"], "title": song["title"]},
                             "error": error, "worker": name}))
    return {"ok": True}
