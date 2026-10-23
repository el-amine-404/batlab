"""LRCLIB (lrclib.net): free lyrics, synced by people, no account.

LRCLIB often holds a song several times, once per way of writing its credit
("Manal", "Manal, Stormy", "Manal & Stormy"), and only one may have timings.
So every entry with the same title, an artist in common and the same length
(within 3 s) is weighed, and synced lyrics win over plain ones.

A source like any other (sources/base.py): its address is a setting, so a
mirror of LRCLIB works the same.
"""

import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from .. import __version__, lyricsfiles
from ..notifications.base import Field
from .base import Found, LyricsSource, SongQuery

API = "https://lrclib.net/api"
USER_AGENT = f"Stanzarr/{__version__} (https://github.com/el-amine-404/batlab)"
MAX_BYTES = 4 * 1024 * 1024
LENGTH_SLACK = 3.5
NOISE = re.compile(r"(?i)\s*[\(\[][^\)\]]*\b(?:feat|ft|with|prod|remaster|version|edit|mix|explicit|clean)\b[^\)\]]*[\)\]]")
SPLIT = re.compile(r"(?i)\s+(?:feat\.?|ft\.?|featuring|with|x|&|,|et|و)\s+|\s*,\s*")

_lock = threading.Lock()
_last = 0.0


def simple(text: str) -> str:
    text = unicodedata.normalize("NFKD", NOISE.sub("", text or "")).casefold()
    return re.sub(r"[^\w]+", "", "".join(c for c in text if not unicodedata.combining(c)))


def names(artist: str) -> set[str]:
    return {simple(p) for p in SPLIT.split(artist or "") if simple(p)}


def same_title(one: str, other: str) -> bool:
    a, b = simple(one), simple(other)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)))


def _get(path: str, params: dict, attempts: int = 3, api: str = API):
    global _last
    with _lock:  # one request a second: someone else's free service
        wait = 1.0 - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
    request = urllib.request.Request(f"{api}/{path}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code in (429, 503) and attempts > 1:
            time.sleep(5 * (4 - attempts))
            return _get(path, params, attempts - 1, api)
        raise
    if len(body) > MAX_BYTES:
        raise ValueError("LRCLIB answer too large")
    return json.loads(body)


def _synced(entry: dict) -> str:
    text = lyricsfiles.clean(entry.get("syncedLyrics") or "")
    lyrics = lyricsfiles.parse_lrc(text)
    return text if lyrics.synced and all(l.t is not None for l in lyrics.lines) else ""


def pick(results: list[dict], seconds: float, title: str = "", artist: str = "") -> dict | None:
    best, best_key = None, None
    for entry in results or []:
        try:
            # The length unknown (a song asked for by its name): any length will do.
            gap = abs(float(entry.get("duration") or 0) - seconds) if seconds > 0 else 0.0
        except (TypeError, ValueError):
            continue
        if gap >= LENGTH_SLACK:
            continue
        if title and not same_title(entry.get("trackName") or "", title):
            continue
        if artist and not names(entry.get("artistName") or "") & names(artist):
            continue
        if _synced(entry):
            rank = 0
        elif (entry.get("plainLyrics") or "").strip() or entry.get("instrumental"):
            rank = 1
        else:
            continue
        key = (rank, gap)
        if best_key is None or key < best_key:
            best, best_key = entry, key
    return best


def lookup(artist: str, title: str, album: str, seconds: float, api: str = API) -> tuple[str, str, dict | None]:
    """("synced" | "plain" | "instrumental" | "none", text, LRCLIB's entry)."""
    candidates = []
    # The exact match needs the length; without it, only the searches.
    if seconds > 0 and artist:
        params = {"artist_name": artist, "track_name": title, "duration": int(round(seconds))}
        if album:
            params["album_name"] = album
        exact = _get("get", params, api=api)
        if exact:
            candidates.append(exact)
    if artist:
        candidates += _get("search", {"artist_name": artist, "track_name": title}, api=api) or []
    if not any(_synced(c) and (seconds <= 0 or abs(float(c.get("duration") or 0) - seconds) < LENGTH_SLACK)
               for c in candidates):
        candidates += _get("search", {"track_name": title}, api=api) or []
    found = pick(candidates, seconds, title, artist)
    if not found:
        return "none", "", None
    if found.get("instrumental"):
        return "instrumental", "", found
    synced = _synced(found)
    if synced:
        return "synced", synced, found
    plain = lyricsfiles.clean(found.get("plainLyrics") or "")
    return ("plain", plain, found) if plain.strip() else ("none", "", None)


class LRCLIB(LyricsSource):
    key = "lrclib"
    label = "LRCLIB"
    description = "Free lyrics, many synced by people, no account. Asked once a second at most."
    homepage = "https://lrclib.net"
    fields = (
        Field("url", "Address", "url", required=False, default=API,
              help="LRCLIB's API, or a mirror of it.", placeholder=API),
    )

    def search(self, song: SongQuery, config: dict) -> Found:
        api = (config.get("url") or API).rstrip("/")
        kind, text, entry = lookup(song.artist, song.title, song.album, song.duration, api)
        entry = entry if isinstance(entry, dict) else {"id": entry} if entry else None
        matched = {"artist": entry.get("artistName"), "title": entry.get("trackName"),
                   "duration": entry.get("duration")} if entry and entry.get("trackName") else None
        return Found(kind, text, f"lrclib id: {entry['id']}" if entry and entry.get("id") else None, matched)
