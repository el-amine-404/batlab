"""Lyrics for any song, for the other apps (clipseerr's lyrics of a video,
videodrop's Lyrics subtitle track): Stanzarr owns lyrics, they ask it.

The library first (a song there, matched as LRCLIB's entries are: the same
title, an artist in common, the same length within 3.5 s); else the sources'
answer kept from before (LOOKUP_DAYS); else the sources, asked now, their
answer kept. Nothing is written into the library for a song outside it."""

import asyncio
import time

from fastapi import APIRouter, Query

from ... import lyricsfiles, operations
from ...sources import NOTHING, SongQuery
from ...sources.lrclib import LENGTH_SLACK, names, same_title, simple
from .. import schemas
from ..deps import Ctx

router = APIRouter(tags=["Lyrics"])
LOOKUP_DAYS = 30


def _from_library(ctx, query: SongQuery) -> dict | None:
    wanted = names(query.artist)
    for row in ctx.db.all("SELECT id, artist, title, duration FROM songs"):
        if not same_title(row["title"], query.title) or not names(row["artist"]) & wanted:
            continue
        if query.duration and row["duration"] and abs(row["duration"] - query.duration) >= LENGTH_SLACK:
            continue
        lyrics = operations.best_text(ctx.db, row["id"])
        if lyrics is None:
            continue
        origin = lyricsfiles.origin(lyrics).source
        return {"found": True, "synced": lyrics.synced, "from": "library", "song_id": row["id"],
                "origin": origin, "lrc": lyricsfiles.render_lrc(lyrics.lines, []) if lyrics.synced else None,
                "text": lyrics.text}
    return None


def _answer(kind: str, text: str, source: str | None) -> dict:
    if kind == "synced":
        lyrics = lyricsfiles.parse_lrc(text)
        return {"found": True, "synced": True, "from": "source", "source": source, "origin": source,
                "lrc": text, "text": lyrics.text}
    if kind == "plain":
        return {"found": True, "synced": False, "from": "source", "source": source, "text": text}
    return {"found": False, "synced": False, "instrumental": kind == "instrumental",
            "from": "source" if source else "none", "source": source}


def _ask(ctx, query: SongQuery) -> tuple[str, str, str | None, str | None]:
    best, best_label, failed = NOTHING, None, None
    for item, kind in ctx.sources.asked():
        try:
            found = kind.search(query, item["config"])
        except Exception as error:
            failed = error
            continue
        if found.kind == "synced":
            return found.kind, found.text, kind.label, found.reference
        if found.kind != "none" and best.kind == "none":
            best, best_label = found, kind.label
    if best.kind == "none" and failed is not None:
        raise failed
    return best.kind, best.text, best_label, best.reference


@router.get("/lyrics", summary="Lyrics for any song: the library's, else the sources'",
            response_model=schemas.LyricsAnswer, response_model_by_alias=True)
async def lyrics(ctx: Ctx, artist: str = Query(min_length=1, max_length=200),
                 title: str = Query(min_length=1, max_length=200), album: str = Query("", max_length=200),
                 duration: float = Query(0, ge=0, le=24 * 3600, description="Seconds; helps tell versions apart.")):
    query = SongQuery(artist.strip(), title.strip(), album.strip(), duration)
    found = _from_library(ctx, query)
    if found:
        return found
    key = f"{simple(query.artist)}|{simple(query.title)}|{round(duration)}"
    kept = ctx.db.one("SELECT * FROM lookups WHERE key = ? AND at > ?", (key, time.time() - LOOKUP_DAYS * 86400))
    if kept:
        return _answer(kept["kind"], kept["text"], kept["source"] or None)
    try:
        kind, text, label, reference = await asyncio.to_thread(_ask, ctx, query)
    except Exception:
        # No source answered: nothing kept, the next question asks again.
        return _answer("none", "", None)
    ctx.db.run("INSERT INTO lookups (key, at, kind, text, source, reference) VALUES (?, ?, ?, ?, ?, ?) "
               "ON CONFLICT (key) DO UPDATE SET at = excluded.at, kind = excluded.kind, text = excluded.text, "
               "source = excluded.source, reference = excluded.reference",
               (key, time.time(), kind, text, label or "", reference))
    return _answer(kind, text, label)
