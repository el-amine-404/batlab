"""The AI queue and the GPU workers: the jobs as people see them, the
workers' key, and the protocol a worker speaks (stanzarr-worker): check in,
take the next job, fetch its song, report stages, send the voice and the
result. A worker authenticates with the workers' key (X-Stanzarr-Key), not
an account; the protocol answers at /api/v1/worker/… and, for the workers
before 1.0, /api/worker/…."""

import hmac
import os
import time

from batlab_common.accounts import Unauthorized
from fastapi import APIRouter, Depends, Request, Response
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
def cancel(job_id: int, ctx: Ctx):
    if not ctx.db.run("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status IN ('queued','running')",
                      (int(time.time()), job_id)):
        raise NotFound("There's no queued or running job with that id.")
    return {"ok": True}


@router.post("/jobs/{job_id}/retry", summary="Queue a failed or cancelled job again", response_model=schemas.Ok,
             dependencies=[need("ai")])
def retry(job_id: int, ctx: Ctx):
    if not ctx.db.run("UPDATE jobs SET status='queued', worker='', stage='', error='' WHERE id=? AND status IN"
                      " ('failed','cancelled')", (job_id,)):
        raise NotFound("There's no failed or cancelled job with that id.")
    return {"ok": True}


@router.get("/workers", summary="The workers, and their key for who may change settings",
            response_model=schemas.WorkerList)
def workers(ctx: Ctx, who: Who):
    rows = ctx.db.all("SELECT * FROM workers ORDER BY last_seen DESC")
    now = int(time.time())
    for r in rows:
        r["online"] = now - r["last_seen"] < 60
    return {"workers": rows, "key": ctx.worker_key() if who.can("settings") else None,
            "key_fixed": bool(ctx.config.worker_key)}


@router.post("/workers/key", summary="A new workers' key: the workers need it before their next job",
             dependencies=[need("settings")])
def new_key(ctx: Ctx):
    if ctx.config.worker_key:
        raise Conflict("The key is set by STANZARR_WORKER_KEY: change it there.")
    return {"key": ctx.new_worker_key()}


# ---- the protocol -------------------------------------------------------------------------

def worker(request: Request, ctx: Ctx) -> str:
    key = request.headers.get("x-stanzarr-key", "")
    if not key or not hmac.compare_digest(key, ctx.worker_key()):
        raise Unauthorized("Not a worker of this Stanzarr: check the key (Settings, Workers).")
    return request.headers.get("x-stanzarr-worker", "worker")[:64]


def _running(ctx, job_id: int, name: str) -> dict:
    job = ctx.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job or job["status"] != "running" or job["worker"] != name:
        raise Conflict("Not this worker's job, or no longer running.")
    return job


@protocol.post("/heartbeat", summary="A worker checks in (every 20 s)")
def heartbeat(body: dict, ctx: Ctx, name: str = Depends(worker)):
    ctx.db.run("INSERT INTO workers(name, gpu, vram, version, last_seen) VALUES(?, ?, ?, ?, ?)"
               " ON CONFLICT(name) DO UPDATE SET gpu=excluded.gpu, vram=excluded.vram, version=excluded.version,"
               " last_seen=excluded.last_seen",
               (name, str(body.get("gpu", ""))[:120], int(body.get("vram", 0) or 0),
                str(body.get("version", ""))[:32], int(time.time())))
    return {"ok": True}


@protocol.post("/next", summary="The next job, or 204 when there is none")
def next_job(ctx: Ctx, name: str = Depends(worker)):
    with ctx.db.tx() as conn:
        row = conn.execute("SELECT id, song_id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return Response(status_code=204)
        conn.execute("UPDATE jobs SET status='running', worker=?, started_at=?, stage='starting' WHERE id=?",
                     (name, int(time.time()), row["id"]))
    found = song_or_404(ctx, row["song_id"])
    lyrics = operations.best_text(ctx.db, row["song_id"])
    words = [line.s for line in (lyrics.lines if lyrics else []) if line.s.strip()
             and not (line.s.startswith("[") and line.s.endswith("]"))]
    return {"job": row["id"], "song": {k: found[k] for k in ("artist", "title", "album", "duration")},
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
    outcome = operations.save_ai_result(ctx.db, ctx.config, job["song_id"], lines, str(body.get("method", ""))[:200])
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
    ctx.db.event("ai", f"AI sync failed: {error}", job["song_id"])
    song = song_or_404(ctx, job["song_id"])
    ctx.notifier.emit(Event("ai.failed", f"{song['artist']} – {song['title']}: AI sync failed", (error,),
                            ctx.link(f"/songs/{song['id']}"),
                            {"song": {"id": song["id"], "artist": song["artist"], "title": song["title"]},
                             "error": error, "worker": name}))
    return {"ok": True}
