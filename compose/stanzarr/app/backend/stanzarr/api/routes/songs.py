"""The library: the songs, their lyrics files, the editor, the audio."""

import logging
import os
import time
from typing import Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse

from ... import language, library, operations, peaks
from ...domain import Conflict, Invalid, NotFound
from .. import schemas
from ..auth import Who, need
from ..deps import Ctx

router = APIRouter(prefix="/songs", tags=["Library"])
log = logging.getLogger("stanzarr")
_404 = {404: {"model": schemas.Problem}}


def song_or_404(ctx, song_id: int) -> dict:
    found = operations.song_or_none(ctx.db, song_id)
    if not found:
        raise NotFound("There's no such song.")
    return found


@router.get("", summary="The songs, filtered, a page at a time", response_model=schemas.SongList)
def songs(ctx: Ctx, status: schemas.Status | None = None,
          q: str = Query("", max_length=200, description="In the title, the artist and the album."),
          language_code: str = Query("", max_length=12), format: str = Query("", max_length=12),
          to_check: bool = Query(False, description="Only songs with AI lines still to check."),
          sort: Literal["artist", "added", "title"] = "artist",
          page: int = Query(1, ge=1, le=100_000), size: int = Query(50, ge=1, le=200)):
    db = ctx.db
    where, args = [], []
    if status:
        where.append(f"({library.STATUS_SQL}) = ?")
        args.append(status)
    if q.strip():
        where.append("(s.title LIKE ? OR s.artist LIKE ? OR s.album LIKE ?)")
        args += [f"%{q.strip()}%"] * 3
    if language_code:
        where.append("EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.language=?)")
        args.append(language_code)
    if format:
        where.append("EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.format=?)")
        args.append(format)
    if to_check:
        where.append("EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.source='ai' AND l.unsure > l.checked"
                     " AND l.ai_state='unchecked')")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order = {"artist": "s.artist COLLATE NOCASE, s.album COLLATE NOCASE, s.rel",
             "added": "s.added_at DESC, s.id DESC", "title": "s.title COLLATE NOCASE"}[sort]
    total = db.one(f"SELECT COUNT(*) AS n FROM songs s {clause}", args)["n"]
    rows = db.all(
        f"SELECT s.id, s.artist, s.album, s.title, s.duration, s.rel, {library.STATUS_SQL} AS status,"
        " (SELECT GROUP_CONCAT(DISTINCT l.format) FROM lyrics l WHERE l.song_id=s.id) AS formats,"
        " (SELECT GROUP_CONCAT(DISTINCT l.language) FROM lyrics l WHERE l.song_id=s.id AND l.language!='') AS languages,"
        " (SELECT MAX(l.unsure - l.checked) FROM lyrics l WHERE l.song_id=s.id AND l.source='ai') AS to_check,"
        " (SELECT j.status FROM jobs j WHERE j.song_id=s.id ORDER BY j.id DESC LIMIT 1) AS job"
        f" FROM songs s {clause} ORDER BY {order} LIMIT ? OFFSET ?", args + [size, (page - 1) * size])
    for r in rows:
        r["formats"] = sorted((r["formats"] or "").split(",")) if r["formats"] else []
        r["languages"] = [{"code": c, "name": language.name(c)} for c in (r["languages"] or "").split(",") if c]
    return {"total": total, "page": page, "size": size, "songs": rows}


@router.get("/{song_id}", summary="One song: its lyrics files, its last search, its jobs", responses=_404)
def song(song_id: int, ctx: Ctx):
    db, cfg = ctx.db, ctx.config
    found = song_or_404(ctx, song_id)
    files = db.all("SELECT * FROM lyrics WHERE song_id=? ORDER BY synced DESC, format", (song_id,))
    for f in files:
        f["synced"] = bool(f["synced"])
        f["language_name"] = language.name(f["language"])
        f["name"] = "embedded in the audio file" if f["path"].startswith("embedded:") else os.path.basename(f["path"])
    status_row = db.one(f"SELECT {library.STATUS_SQL} AS status FROM songs s WHERE s.id=?", (song_id,))
    return {"song": found | {"status": status_row["status"] if status_row else "missing"}, "files": files,
            "attempt": db.one("SELECT * FROM attempts WHERE song_id=?", (song_id,)),
            "jobs": db.all("SELECT * FROM jobs WHERE song_id=? ORDER BY id DESC LIMIT 5", (song_id,)),
            "events": db.all("SELECT * FROM events WHERE song_id=? ORDER BY id DESC LIMIT 10", (song_id,)),
            "has_voice": os.path.exists(os.path.join(cfg.artifacts_dir, str(song_id), "vocals.ogg")),
            "has_text": operations.best_text(db, song_id) is not None}


@router.post("/{song_id}/search", summary="Ask the sources for this song now", responses=_404,
             dependencies=[need("edit")])
def search(song_id: int, ctx: Ctx):
    song_or_404(ctx, song_id)
    try:
        return {"result": ctx.search(song_id)}
    except Exception as error:
        raise Conflict(f"No source answered: {error}") from error


@router.post("/{song_id}/ai-sync", summary="Queue this song for AI sync", responses=_404,
             dependencies=[need("ai")])
def ai_sync(song_id: int, ctx: Ctx, who: Who):
    db = ctx.db
    found = song_or_404(ctx, song_id)
    if not any(w["gpu"] for w in ctx.workers_online()):
        raise Conflict("No GPU worker is online: AI sync needs one (Settings, Workers).")
    if operations.best_text(db, song_id) is None:
        raise Conflict("AI sync needs the words first: this song has no lyrics to place.")
    if db.one("SELECT 1 FROM jobs WHERE song_id=? AND status IN ('queued','running')", (song_id,)):
        raise Conflict("It is queued already.")
    job = db.insert("INSERT INTO jobs(song_id, kind, status, created_at) VALUES(?, 'ai_sync', 'queued', ?)",
                    (song_id, int(time.time())))
    db.event("ai", f"{found['artist']} – {found['title']}: AI sync queued by {who.name}", song_id)
    return {"job": job}


@router.get("/{song_id}/editor", summary="The lines to edit, and what the song has", responses=_404)
def editor(song_id: int, ctx: Ctx):
    found = song_or_404(ctx, song_id)
    data = operations.editor_lines(ctx.db, song_id)
    return data | {"song": {k: found[k] for k in ("id", "artist", "album", "title", "duration", "rel", "root")},
                   "has_voice": os.path.exists(os.path.join(ctx.config.artifacts_dir, str(song_id), "vocals.ogg")),
                   "read_only": ctx.config.read_only}


@router.put("/{song_id}/lyrics", summary="Save the editor's lines as the song's .lrc", responses=_404,
            dependencies=[need("edit")])
def save(song_id: int, body: dict, ctx: Ctx, who: Who):
    song_or_404(ctx, song_id)
    lines = body.get("lines")
    if not isinstance(lines, list) or not lines or len(lines) > 5000:
        raise Invalid("Send the lines: [{\"t\": seconds, \"s\": text}, …], 5000 at most.")
    try:
        saved = operations.save_from_editor(ctx.db, ctx.config, song_id, lines)
    except operations.ReadOnly as exc:
        raise Conflict("Stanzarr is read-only (STANZARR_READ_ONLY): nothing is written.") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise Invalid("Each line is {\"t\": seconds, \"s\": text}.") from exc
    log.info("%s saved %d lines of song %s", who.name, saved, song_id)
    return {"saved": saved}


def _track(ctx, song_id: int, kind: str) -> str:
    found = song_or_404(ctx, song_id)
    path = os.path.join(ctx.config.artifacts_dir, str(song_id), "vocals.ogg") if kind == "voice" else found["path"]
    if not os.path.exists(path):
        raise NotFound("There's no such track: the voice is there once a worker synced the song.")
    return path


@router.get("/{song_id}/audio/{kind}", summary="The song (mix), or its voice alone", responses=_404,
            response_class=FileResponse)
def audio(song_id: int, kind: Literal["mix", "voice"], ctx: Ctx):
    return FileResponse(_track(ctx, song_id, kind))


@router.get("/{song_id}/peaks/{kind}", summary="The waveform of the mix or the voice", responses=_404)
def song_peaks(song_id: int, kind: Literal["mix", "voice"], ctx: Ctx):
    path = _track(ctx, song_id, kind)
    key = f"{song_id}-{kind}-{int(os.path.getmtime(path))}"
    return Response(peaks.cached(ctx.config.cache_dir, key, path), media_type="application/json")
