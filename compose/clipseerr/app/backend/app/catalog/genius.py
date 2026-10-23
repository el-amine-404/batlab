"""Genius (genius.com): who made a song, from the same API its pages read,
without an account: performers, writers, producers, and the rest of its
credits (mixing, mastering, labels…). It has new songs within days of their
release, before MusicBrainz does.

Two questions a song (its search, then the song), one a second at most."""

import asyncio
import re
import time

import httpx
from batlab_common import titles

API = "https://genius.com/api"
# Its pages' API answers what looks like a browser.
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
           "Accept": "application/json"}
TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_lock = asyncio.Lock()
_last = 0.0


async def _get(http: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    global _last
    async with _lock:
        pause = 1.0 - (time.monotonic() - _last)
        if pause > 0:
            await asyncio.sleep(pause)
        _last = time.monotonic()
        response = await http.get(f"{API}/{path}", params=params, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return (response.json() or {}).get("response") or {}


def credits_of(song: dict) -> list[dict]:
    """A Genius song's credits, as the credits panel words them."""
    def names(people) -> list[str]:
        return list(dict.fromkeys(p.get("name") for p in people or [] if p.get("name")))

    out = []
    performers = names([song.get("primary_artist") or {}, *(song.get("featured_artists") or [])])
    for role, people in (("Performed by", performers), ("Written by", names(song.get("writer_artists"))),
                         ("Produced by", names(song.get("producer_artists")))):
        if people:
            out.append({"role": role, "names": people})
    for custom in song.get("custom_performances") or []:
        people = names(custom.get("artists"))
        if custom.get("label") and people:
            out.append({"role": custom["label"], "names": people})
    # Only the performers: that is no credit at all.
    return out if any(c["role"] != "Performed by" for c in out) else []


async def credits(http: httpx.AsyncClient, artist: str, title: str) -> dict:
    """{"credits": [{"role", "names"}], "provider": "Genius"}; no credits when it has none.
    A video's longer title ("Thin Ice - Tina Tuesday Freestyle") is asked by its first part too."""
    found = await _credits(http, artist, title)
    shorter = re.split(r"\s[-–—|]\s", title)[0].strip()
    if not found["credits"] and shorter and shorter != title:
        found = await _credits(http, artist, shorter)
    return found


async def _credits(http: httpx.AsyncClient, artist: str, title: str) -> dict:
    found = await _get(http, "search/multi", {"q": f"{artist} {title}"})
    for section in found.get("sections") or []:
        if section.get("type") not in ("top_hit", "song"):
            continue
        for hit in section.get("hits") or []:
            result = hit.get("result") or {}
            if hit.get("type") != "song" or not titles.agrees(title, artist, result.get("title") or "",
                                                              (result.get("primary_artist") or {}).get("name") or ""):
                continue
            song = (await _get(http, f"songs/{result['id']}")).get("song") or {}
            made = credits_of(song)
            return {"credits": made, "provider": "Genius" if made else None}
    return {"credits": [], "provider": None}
