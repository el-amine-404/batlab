"""The library: the songs, their lyrics files, the editor, the audio."""

import json
import logging
import os
import time
import urllib.parse
from typing import Literal

import mutagen
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse

from ... import language, library, lyricsfiles, operations, peaks
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


SORTS = {"artist": "s.artist COLLATE NOCASE {d}, s.album COLLATE NOCASE, s.rel",
         "title": "s.title COLLATE NOCASE {d}, s.artist COLLATE NOCASE",
         "album": "s.album COLLATE NOCASE {d}, s.rel",
         "duration": "s.duration {d}, s.id",
         "added": "s.added_at {d}, s.id {d}",
         "status": "status_rank {d}, s.artist COLLATE NOCASE"}
_ORDER = {"synced": 0, "ai_corrected": 1, "ai_checked": 2, "ai_unchecked": 3, "plain": 4, "instrumental": 5, "missing": 6}
_STATUS_RANK = "CASE status " + " ".join(f"WHEN '{k}' THEN {v}" for k, v in _ORDER.items()) + " END"


def _filters(status, q, language_code, format, made_by, artist, album, to_check) -> tuple[str, list]:
    where, args = [], []
    if status:
        where.append(f"({library.STATUS_SQL}) IN ({', '.join('?' * len(status))})")
        args += status
    if q.strip():
        where.append("(s.title LIKE ? OR s.artist LIKE ? OR s.album LIKE ?)")
        args += [f"%{q.strip()}%"] * 3
    for column, value in (("language", language_code), ("format", format), ("source", made_by)):
        if value:
            where.append(f"EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.{column}=?)")
            args.append(value)
    if artist:
        where.append("(s.artist = ? COLLATE NOCASE OR s.albumartist = ? COLLATE NOCASE)")
        args += [artist, artist]
    if album:
        where.append("s.album = ? COLLATE NOCASE")
        args.append(album)
    if to_check:
        where.append("EXISTS(SELECT 1 FROM lyrics l WHERE l.song_id=s.id AND l.source='ai' AND l.unsure > l.checked"
                     " AND l.ai_state='unchecked')")
    return ("WHERE " + " AND ".join(where)) if where else "", args


@router.get("", summary="The songs, filtered and sorted, a page at a time", response_model=schemas.SongList)
def songs(ctx: Ctx, status: list[schemas.Status] = Query([], description="Any of these; repeat it for several."),
          q: str = Query("", max_length=200, description="In the title, the artist and the album."),
          language_code: str = Query("", max_length=12), format: str = Query("", max_length=12),
          made_by: str = Query("", max_length=20, description="Who made a file: person, ai, lrclib, netease…"),
          artist: str = Query("", max_length=200), album: str = Query("", max_length=200),
          to_check: bool = Query(False, description="Only songs with AI lines still to check."),
          sort: Literal["artist", "title", "album", "duration", "added", "status"] = "artist",
          direction: Literal["asc", "desc"] = "asc",
          page: int = Query(1, ge=1, le=100_000), size: int = Query(50, ge=1, le=200)):
    db = ctx.db
    clause, args = _filters(status, q, language_code, format, made_by, artist, album, to_check)
    order = SORTS[sort].format(d="DESC" if direction == "desc" else "ASC")
    total = db.one(f"SELECT COUNT(*) AS n FROM songs s {clause}", args)["n"]
    rows = db.all(
        f"SELECT * FROM (SELECT s.id, s.artist, s.album, s.title, s.duration, s.rel, s.added_at, {library.STATUS_SQL} AS status,"
        " (SELECT GROUP_CONCAT(DISTINCT l.format) FROM lyrics l WHERE l.song_id=s.id) AS formats,"
        " (SELECT GROUP_CONCAT(DISTINCT l.language) FROM lyrics l WHERE l.song_id=s.id AND l.language!='') AS languages,"
        " (SELECT GROUP_CONCAT(DISTINCT l.source) FROM lyrics l WHERE l.song_id=s.id) AS made_by,"
        " (SELECT MAX(l.unsure - l.checked) FROM lyrics l WHERE l.song_id=s.id AND l.source='ai' AND l.ai_state='unchecked') AS to_check,"
        " (SELECT j.status FROM jobs j WHERE j.song_id=s.id ORDER BY j.id DESC LIMIT 1) AS job"
        f" FROM songs s {clause}) AS s ORDER BY {order.replace('status_rank', _STATUS_RANK)} LIMIT ? OFFSET ?",
        args + [size, (page - 1) * size])
    for r in rows:
        r["formats"] = sorted((r["formats"] or "").split(",")) if r["formats"] else []
        r["made_by"] = sorted((r["made_by"] or "").split(",")) if r["made_by"] else []
        r["languages"] = [{"code": c, "name": language.name(c)} for c in (r["languages"] or "").split(",") if c]
    return {"total": total, "page": page, "size": size, "songs": rows}


@router.get("/facets", summary="What the library's filters can choose from, with how many songs each")
def facets(ctx: Ctx):
    db = ctx.db
    count = "SELECT {col} AS value, COUNT(DISTINCT song_id) AS songs FROM lyrics WHERE {col}!='' GROUP BY {col} ORDER BY songs DESC"
    return {
        "languages": [{"value": r["value"], "label": language.name(r["value"]), "songs": r["songs"]}
                      for r in db.all(count.format(col="language"))],
        "formats": db.all(count.format(col="format")),
        "made_by": db.all(count.format(col="source")),
        "artists": db.all("SELECT artist AS value, COUNT(*) AS songs FROM songs WHERE artist!='' GROUP BY artist"
                          " COLLATE NOCASE ORDER BY artist COLLATE NOCASE"),
        "albums": db.all("SELECT album AS value, artist, COUNT(*) AS songs FROM songs WHERE album!='' GROUP BY album"
                         " COLLATE NOCASE, artist COLLATE NOCASE ORDER BY album COLLATE NOCASE"),
    }


@router.get("/history", summary="The coverage, day by day: songs by status at each day's last scan")
def history(ctx: Ctx, days: int = Query(90, ge=1, le=3650)):
    rows = ctx.db.all("SELECT day, counts FROM coverage_daily ORDER BY day DESC LIMIT ?", (days,))
    return [{"day": r["day"], "counts": json.loads(r["counts"])} for r in reversed(rows)]


@router.get("/{song_id}", summary="One song: its lyrics files, its last search, its jobs", responses=_404)
def song(song_id: int, ctx: Ctx):
    db, cfg = ctx.db, ctx.config
    found = song_or_404(ctx, song_id)
    files = db.all("SELECT * FROM lyrics WHERE song_id=? ORDER BY synced DESC, format", (song_id,))
    for f in files:
        f["synced"] = bool(f["synced"])
        f["language_name"] = language.name(f["language"])
        f["name"] = "embedded in the audio file" if f["path"].startswith("embedded:") else os.path.basename(f["path"])
    status_row = db.one(f"SELECT {library.STATUS_SQL} AS status, {library.RANK_SQL} AS rank FROM songs s WHERE s.id=?",
                        (song_id,))
    has_text = operations.best_text(db, song_id) is not None
    queued = db.one("SELECT 1 FROM jobs WHERE song_id=? AND status IN ('queued','running')", (song_id,))
    online_workers = ctx.workers_online()
    active_worker = online_workers[0] if online_workers else None
    # Why an action can't be done now, said beside it on the page (None: it can).
    ai_blocked = ("stanzarr is read-only: nothing is written." if cfg.read_only
                  else "Queued already: see Activity." if queued
                  else "AI sync places words already there: this song has none yet. Ask the sources, or write them."
                  if not has_text
                  else "No GPU worker is online: start one (Settings, Workers)." if not any(w["gpu"] for w in online_workers)
                  else None)
    ai_generate_blocked = ("stanzarr is read-only: nothing is written." if cfg.read_only
                           else "Queued already: see Activity." if queued
                           else "No GPU worker is online: start one (Settings, Workers)." if not any(w["gpu"] for w in online_workers)
                           else None)
    search_blocked = None if ctx.sources.asked() else "No source is on: add one in Settings, Sources."
    return {"song": found | {"status": status_row["status"] if status_row else "missing"},
            "quality": {"rank": status_row["rank"] if status_row else 0,
                        "cutoff": library.LEVELS[ctx.settings().cutoff]},
            "ai_blocked": ai_blocked, "ai_generate_blocked": ai_generate_blocked, "search_blocked": search_blocked,
            "files": files,
            "attempt": db.one("SELECT * FROM attempts WHERE song_id=?", (song_id,)),
            "jobs": db.all("SELECT * FROM jobs WHERE song_id=? ORDER BY id DESC LIMIT 5", (song_id,)),
            "events": [e | {"ok": bool(e["ok"])} for e in db.all("SELECT * FROM events WHERE song_id=? ORDER BY id DESC LIMIT 10",
                                                                 (song_id,))],
            "has_voice": os.path.exists(os.path.join(cfg.artifacts_dir, str(song_id), "vocals.ogg")),
            "has_text": has_text,
            "active_worker": active_worker,
            "workers_online": online_workers}


@router.post("/{song_id}/search", summary="Ask the sources for this song now", responses=_404,
             dependencies=[need("edit")])
def search(song_id: int, ctx: Ctx, who: Who):
    song_or_404(ctx, song_id)
    try:
        return {"result": ctx.search(song_id, who.name)}
    except Exception as error:
        raise Conflict(f"No source answered: {error}") from error


@router.post("/{song_id}/ai-sync", summary="Queue this song for AI sync (timing alignment)", responses=_404,
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
    db.event("ai", f"{found['artist']} – {found['title']}: AI sync queued", song_id, who.name)
    return {"job": job}


@router.post("/{song_id}/ai-generate", summary="Queue this song for AI generation (transcription and sync)", responses=_404,
             dependencies=[need("ai")])
def ai_generate(song_id: int, ctx: Ctx, who: Who):
    db = ctx.db
    found = song_or_404(ctx, song_id)
    if not any(w["gpu"] for w in ctx.workers_online()):
        raise Conflict("No GPU worker is online: AI generation needs one (Settings, Workers).")
    if db.one("SELECT 1 FROM jobs WHERE song_id=? AND status IN ('queued','running')", (song_id,)):
        raise Conflict("It is queued already.")
    job = db.insert("INSERT INTO jobs(song_id, kind, status, created_at) VALUES(?, 'ai_generate', 'queued', ?)",
                    (song_id, int(time.time())))
    db.event("ai", f"{found['artist']} – {found['title']}: AI generation queued", song_id, who.name)
    return {"job": job}


@router.post("/{song_id}/lyrics/upload", summary="Upload a lyrics file (.lrc or .txt) for this song", responses=_404,
             dependencies=[need("edit")])
async def upload_lyrics(song_id: int, request: Request, ctx: Ctx, who: Who):
    found = song_or_404(ctx, song_id)
    if ctx.config.read_only:
        raise Conflict("Stanzarr is read-only (STANZARR_READ_ONLY): nothing is written.")

    content_type = request.headers.get("content-type", "")
    filename = request.headers.get("x-filename", "lyrics.lrc")
    raw_text = ""
    if "application/json" in content_type:
        try:
            body = await request.json()
            filename = str(body.get("filename") or filename)
            raw_text = str(body.get("text") or "")
        except Exception:
            raise Invalid("Send valid JSON with {\"filename\": ..., \"text\": ...}.")
    else:
        body_bytes = await request.body()
        raw_text = body_bytes.decode("utf-8", errors="replace")

    if not raw_text.strip() or len(raw_text.encode("utf-8")) > lyricsfiles.MAX_BYTES:
        raise Invalid(f"Send lyrics text under {lyricsfiles.MAX_BYTES // 1024} KB.")

    checked = lyricsfiles.sanitize(raw_text, found["duration"])
    if checked.refused:
        raise Invalid(f"Refused: {checked.refused}")
    cleaned_text = checked.text

    ext = os.path.splitext(filename)[1].lower()
    if ext not in lyricsfiles.SIDECARS:
        ext = ".lrc" if lyricsfiles.STAMP.search(cleaned_text) else ".txt"

    target_path = operations.sidecar(found, ext)
    if os.path.exists(target_path):
        operations._backup(ctx.config, found, target_path)

    if ext == ".lrc":
        parsed = lyricsfiles.parse_lrc(cleaned_text)
        tags = [f"[{k}:{v}]" for k, v in parsed.tags.items() if k.lower() != "by"]
        tags.insert(0, "[by:User uploaded]")
        final_content = lyricsfiles.render_lrc(parsed.lines, tags)
    else:
        final_content = cleaned_text

    lyricsfiles.write_atomic(target_path, final_content)
    ctx.db.run(
        "INSERT INTO written(path, source, at) VALUES(?, 'user_uploaded', ?)"
        " ON CONFLICT(path) DO UPDATE SET source='user_uploaded', at=excluded.at",
        (target_path, time.time()),
    )
    library.sync_lyrics(ctx.db, song_id, found["path"], force=True)
    ctx.db.run("UPDATE lyrics SET source='user_uploaded' WHERE song_id=? AND path=?", (song_id, target_path))
    ctx.db.event("lyrics", f"{found['artist']} – {found['title']}: lyrics uploaded by user ({ext})", song_id, who.name)
    return {"ok": True, "filename": os.path.basename(target_path), "source": "user_uploaded"}


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
        saved = operations.save_from_editor(ctx.db, ctx.config, song_id, lines, who.name)
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


# ---- what a song has: its cover, its lyrics versions -----------------------------------

COVERS = ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png")


def _embedded_picture(path: str) -> tuple[bytes, str] | None:
    """The cover in the audio file's tags: ID3's APIC, FLAC's and Ogg's pictures, MP4's covr."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None
    pictures = getattr(audio, "pictures", None) or []
    if pictures:
        return pictures[0].data, pictures[0].mime or "image/jpeg"
    tags = audio.tags or {}
    try:
        for key in list(tags.keys()):
            if str(key).startswith("APIC"):
                return tags[key].data, tags[key].mime or "image/jpeg"
        if "covr" in tags and tags["covr"]:
            cover = tags["covr"][0]
            return bytes(cover), "image/png" if getattr(cover, "imageformat", 0) == 14 else "image/jpeg"
    except Exception:
        return None
    return None


@router.get("/{song_id}/cover", summary="The song's cover: the one in its file, else the folder's",
            response_class=Response, responses=_404)
def cover(song_id: int, ctx: Ctx):
    """Read once, then kept (until the file changes): never asked of the network."""
    found = song_or_404(ctx, song_id)
    folder = os.path.join(ctx.config.cache_dir, "covers")
    kept = os.path.join(folder, f"{song_id}-{found['mtime']}")
    headers = {"Cache-Control": "private, max-age=86400"}
    for ext, mime in ((".jpg", "image/jpeg"), (".png", "image/png")):
        if os.path.exists(kept + ext):
            return FileResponse(kept + ext, media_type=mime, headers=headers)
    if os.path.exists(kept + ".none"):
        raise NotFound("This song has no cover.")
    os.makedirs(folder, exist_ok=True)
    picture = _embedded_picture(found["path"])
    if picture is None:
        for name in COVERS:
            beside = os.path.join(os.path.dirname(found["path"]), name)
            if os.path.isfile(beside) and os.path.getsize(beside) < 8 * 1024 * 1024:
                with open(beside, "rb") as handle:
                    picture = handle.read(), "image/png" if name.endswith(".png") else "image/jpeg"
                break
    if picture is None or len(picture[0]) > 8 * 1024 * 1024:
        open(kept + ".none", "w").close()
        raise NotFound("This song has no cover.")
    data, mime = picture
    ext = ".png" if "png" in mime else ".jpg"
    with open(kept + ext, "wb") as out:
        out.write(data)
    return Response(data, media_type="image/png" if ext == ".png" else "image/jpeg", headers=headers)


def _version(ctx, song_id: int, file_id: int) -> tuple[dict, dict]:
    found = song_or_404(ctx, song_id)
    row = ctx.db.one("SELECT * FROM lyrics WHERE id=? AND song_id=?", (file_id, song_id))
    if not row:
        raise NotFound("This song has no such lyrics file.")
    return found, row


def _read_version(found: dict, row: dict):
    if row["path"].startswith("embedded:"):
        return lyricsfiles.read_embedded(found["path"])
    try:
        return lyricsfiles.read_sidecar(row["path"], row["format"])
    except OSError as exc:
        raise NotFound("That file is gone: the next scan forgets it.") from exc


@router.get("/{song_id}/files/{file_id}/lines", summary="One version of the song's lyrics, line by line",
            responses=_404)
def version_lines(song_id: int, file_id: int, ctx: Ctx):
    found, row = _version(ctx, song_id, file_id)
    lyrics = _read_version(found, row)
    return {"synced": bool(lyrics and lyrics.synced),
            "lines": [{"t": line.t, "s": line.s} for line in (lyrics.lines if lyrics else [])]}


@router.get("/{song_id}/files/{file_id}", summary="Download one version: the file itself, or the embedded "
            "lyrics as .lrc or .txt", response_class=Response, responses=_404)
def download(song_id: int, file_id: int, ctx: Ctx):
    found, row = _version(ctx, song_id, file_id)
    stem = os.path.splitext(os.path.basename(found["path"]))[0]
    if row["path"].startswith("embedded:"):
        lyrics = _read_version(found, row)
        if not lyrics:
            raise NotFound("The file's embedded lyrics are gone.")
        synced = lyrics.synced
        body = lyricsfiles.render_lrc(lyrics.lines, []) if synced else lyrics.text + "\n"
        name = f"{stem}.{'lrc' if synced else 'txt'}"
        return Response(body, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})
    if not os.path.isfile(row["path"]):
        raise NotFound("That file is gone: the next scan forgets it.")
    return FileResponse(row["path"], media_type="text/plain; charset=utf-8", filename=os.path.basename(row["path"]))
