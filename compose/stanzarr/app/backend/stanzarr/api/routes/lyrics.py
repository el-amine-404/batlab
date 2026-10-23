"""Lyrics for any song, for the other apps (clipseerr's lyrics of a video,
videodrop's Lyrics subtitle track): Stanzarr owns lyrics, they ask it.

The library first (a song there, matched as LRCLIB's entries are: the same
title, an artist in common, the same length within 3.5 s); else the sources'
answer kept from before (LOOKUP_DAYS); else the sources, asked now, their
answer kept. Nothing is written into the library for a song outside it."""

import asyncio
import time

import html
import json
import re
import urllib.parse

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ... import lyricsfiles, operations
from ...domain import Invalid
from ...sources import NOTHING, Found, SongQuery
from ...sources import http as web
from ...sources.lrclib import LENGTH_SLACK, names, same_title, simple
from .. import schemas
from ..auth import Who
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
        if found.kind in ("synced", "plain"):
            checked = lyricsfiles.sanitize(found.text, query.duration)
            if checked.refused:
                continue
            found = Found(found.kind, checked.text, found.reference)
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


# ---- Find lyrics: a song's name, or a link to it ------------------------------------

YOUTUBE_OEMBED = "https://www.youtube.com/oembed"
# Read by their page's title: public music sites only.
PAGES = ("soundcloud.com", "bandcamp.com", "tidal.com", "music.amazon.com", "music.amazon.co.uk", "music.amazon.fr",
         "music.amazon.de")
# What video titles add around a song's name.
NOISE = re.compile(r"\s*[\(\[](official|lyrics?|audio|video|music video|hd|hq|4k|visuali[sz]er|clip officiel)[^\)\]]*[\)\]]",
                   re.IGNORECASE)
FEATURING = re.compile(r"\s*(\((feat|ft|featuring)\.?\s[^)]*\)|\[(feat|ft|featuring)\.?\s[^\]]*\]|\s(feat|ft|featuring)\.?\s.*$)",
                       re.IGNORECASE)
META = re.compile(r'<meta\s+(?:property|name)="(og:title|og:description|music:duration)"\s+content="([^"]*)"', re.IGNORECASE)


class FindIn(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="A song's name (Artist - Title, or a title), or a "
                                                                 "link to it: YouTube, Spotify, Apple Music, Deezer…")


def _split(name: str) -> tuple[str, str]:
    name = NOISE.sub("", name).strip()
    for dash in (" - ", " – ", " — ", " | "):
        if dash in name:
            artist, _, title = name.partition(dash)
            return artist.strip(), FEATURING.sub("", title).strip()
    return "", FEATURING.sub("", name).strip()


def _song(artist: str, title: str, seconds: float, via: str, cover: str | None = None) -> dict:
    return {"artist": artist.strip(), "title": FEATURING.sub("", NOISE.sub("", title)).strip(), "duration": seconds,
            "via": via, "cover": cover}


def _spotify(link: str) -> dict | None:
    found = re.search(r"/track/([A-Za-z0-9]{22})", link)
    if not found:
        return None
    page = web.get(f"https://open.spotify.com/embed/track/{found.group(1)}", browser=True).decode("utf-8", "replace")
    data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.DOTALL)
    entity = json.loads(data.group(1))["props"]["pageProps"]["state"]["data"]["entity"] if data else {}
    artists = [a.get("name") or "" for a in entity.get("artists") or []]
    if not entity.get("name"):
        return None
    cover = ((entity.get("visualIdentity") or {}).get("image") or [{}])[0].get("url")
    return _song(artists[0] if artists else "", entity["name"], (entity.get("duration") or 0) / 1000, "spotify", cover)


def _deezer(link: str) -> dict | None:
    found = re.search(r"/track/(\d+)", link)
    if not found:
        return None
    track = web.get_json(f"https://api.deezer.com/track/{found.group(1)}")
    if not track or not track.get("title"):
        return None
    return _song((track.get("artist") or {}).get("name") or "", track["title"], track.get("duration") or 0, "deezer",
                 (track.get("album") or {}).get("cover_medium"))


def _apple(link: str) -> dict | None:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
    found = (query.get("i") or [None])[0] or (re.search(r"/song/[^/]+/(\d+)", link) or [None, None])[1]
    if not found:
        return None
    results = (web.get_json("https://itunes.apple.com/lookup", {"id": found, "entity": "song"}) or {}).get("results")
    track = next((r for r in results or [] if r.get("kind") == "song"), None)
    if not track:
        return None
    return _song(track.get("artistName") or "", track.get("trackName") or "", (track.get("trackTimeMillis") or 0) / 1000,
                 "apple music", track.get("artworkUrl100"))


def _youtube(link: str) -> dict | None:
    page = web.get_json(YOUTUBE_OEMBED, {"url": link, "format": "json"}) or {}
    artist, title = _split(page.get("title") or "")
    if not artist:
        # A channel named "Artist - Topic" or "ArtistVEVO" is the artist.
        artist = re.sub(r"(\s*-\s*Topic|VEVO)$", "", page.get("author_name") or "").strip()
    return _song(artist, title, 0, "youtube", page.get("thumbnail_url")) if title else None


def _page(link: str) -> dict | None:
    """Any other page: its og:title (often "Artist - Title"), and its description's artist when it has one."""
    page = web.get(link, browser=True)[:300_000].decode("utf-8", "replace")
    meta = {k.lower(): html.unescape(v) for k, v in META.findall(page)}
    artist, title = _split(meta.get("og:title") or "")
    try:
        seconds = float(meta.get("music:duration") or 0)
    except ValueError:
        seconds = 0
    return _song(artist, title, seconds, "page") if title else None


def _resolve(link: str) -> dict:
    """What song a link is, asked of that site only. https and http to these sites only."""
    parts = urllib.parse.urlsplit(link)
    if parts.scheme not in ("http", "https") or parts.port not in (None, 80, 443) or parts.username:
        raise Invalid("That isn't a link stanzarr reads: try the song's name.")
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host in ("youtube.com", "music.youtube.com", "youtu.be"):
        how = _youtube
    elif host == "open.spotify.com":
        how = _spotify
    elif host.endswith("deezer.com"):
        how = _deezer
    elif host in ("music.apple.com", "itunes.apple.com"):
        how = _apple
    elif any(host == site or host.endswith("." + site) for site in PAGES):
        how = _page
    else:
        # Only these sites: a link anywhere else could be a service inside the network.
        raise Invalid("Links from YouTube, Spotify, Deezer, Apple Music, SoundCloud, Bandcamp, Tidal or Amazon Music "
                      "only: for anything else, the song's name.")
    try:
        found = how(link)
    except Exception as error:
        raise Invalid(f"That link couldn't be read ({web.explain(error)}): try the song's name.") from error
    if not found or not found["title"]:
        raise Invalid("That link isn't a song stanzarr can tell: try the song's name.")
    return found


def _find(ctx, text: str) -> dict:
    text = text.strip()
    resolved = None
    if re.match(r"[a-z][a-z0-9+.-]*://", text, re.IGNORECASE):
        resolved = _resolve(text)
        artist, title, seconds = resolved["artist"], resolved["title"], resolved["duration"]
    else:
        (artist, title), seconds = _split(text), 0.0
    query = SongQuery(artist, title, "", seconds)
    answers = []
    library = _from_library(ctx, query) if artist else None
    if library:
        answers.append(library | {"label": "Your library"})
    for item, kind in ctx.sources.asked():
        try:
            found = kind.search(query, item["config"])
        except Exception as error:
            answers.append({"found": False, "synced": False, "from": "source", "source": kind.label,
                            "label": item["name"], "error": web.explain(error)})
            continue
        if found.kind in ("synced", "plain"):
            checked = lyricsfiles.sanitize(found.text, seconds)
            if checked.refused:
                answers.append({"found": False, "synced": False, "from": "source", "source": kind.label,
                                "label": item["name"], "error": f"refused by the filter: {checked.refused}"})
                continue
            found = Found(found.kind, checked.text, found.reference)
        answers.append(_answer(found.kind, found.text, kind.label) | {"label": item["name"]})
    return {"artist": artist, "title": title, "resolved": resolved, "answers": answers}


@router.post("/find", summary="Find lyrics for a song's name or a link: your library, then every source on")
async def find(body: FindIn, ctx: Ctx, who: Who):
    try:
        result = await asyncio.to_thread(_find, ctx, body.query)
    except Invalid as error:
        ctx.db.event("search", f"Find lyrics: {body.query[:200]}", actor=who.name, ok=False, detail=str(error))
        raise
    good = [a for a in result["answers"] if a.get("found")]
    ctx.db.event("search", f"Find lyrics: {result['artist']} – {result['title']}".replace(" –  ", " ").strip(),
                 actor=who.name, ok=bool(good),
                 detail=", ".join(f"{a.get('label')}: {'synced' if a.get('synced') else 'words' if a.get('found') else a.get('error') or 'nothing'}"
                                  for a in result["answers"]))
    return result
