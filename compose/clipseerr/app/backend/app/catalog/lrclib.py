"""LRCLIB (lrclib.net): song lyrics, timed (LRC) or plain, free and without
a key. Its search answers several takes of a song, some of them wrong (an
"instrumental" one, another length): the best is the same title and artist,
of about the song's length, with timed lines when one has them."""

import re

import httpx
from batlab_common import titles

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
HEADERS = {"User-Agent": "clipseerr (a home server's music video app; https://github.com/el-amine-404/batlab)"}
# A song's length may differ this much between LRCLIB and YouTube Music.
SLACK = 8
_LRC = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(text: str) -> list[dict]:
    """ "[00:12.34] words" lines → [{"start": 12.34, "text": "words"}], in order."""
    lines = []
    for raw in (text or "").splitlines():
        stamps = list(_LRC.finditer(raw))
        if not stamps:
            continue
        words = raw[stamps[-1].end():].strip()
        for stamp in stamps:
            lines.append({"start": int(stamp.group(1)) * 60 + float(stamp.group(2)), "text": words})
    return sorted(lines, key=lambda line: line["start"])


def best(entries: list[dict], title: str, artist: str, seconds: int | None) -> dict | None:
    picked, score = None, -1.0
    for entry in entries:
        if not titles.agrees(title, artist, entry.get("trackName") or "", entry.get("artistName") or ""):
            continue
        if not titles.same_version(title, entry.get("trackName") or ""):
            continue
        gap = abs((entry.get("duration") or 0) - seconds) if seconds and entry.get("duration") else None
        if gap is not None and gap > SLACK:
            continue
        has_words = bool(entry.get("syncedLyrics") or entry.get("plainLyrics"))
        value = (2 if entry.get("syncedLyrics") else 1 if has_words else 0) + (1 - (gap or 0) / (SLACK + 1)) * 0.5
        if value > score:
            picked, score = entry, value
    return picked


async def find(http: httpx.AsyncClient, title: str, artist: str, seconds: int | None) -> dict | None:
    """{"synced", "plain", "instrumental", "source"}, or None when LRCLIB has nothing."""
    response = await http.get("https://lrclib.net/api/search", params={"track_name": title, "artist_name": artist},
                              headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    entry = best(response.json() or [], title, artist, seconds)
    if not entry:
        return None
    synced = parse_lrc(entry.get("syncedLyrics") or "")
    plain = (entry.get("plainLyrics") or "").strip() or "\n".join(line["text"] for line in synced) or None
    if not synced and not plain:
        return {"synced": [], "plain": None, "instrumental": bool(entry.get("instrumental")), "source": "LRCLIB"}
    return {"synced": synced, "plain": plain, "instrumental": False, "source": "LRCLIB"}
