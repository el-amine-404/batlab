"""Lyrics from LRCLIB (lrclib.net), written next to the track for Navidrome.

Navidrome reads a sidecar with the track's name: `.lrc` for time-synced
lyrics, `.txt` for plain ones (its LyricsPriority default lists both). LRCLIB
is free, needs no key, and asks only for a User-Agent naming the client.
"""

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

API = "https://lrclib.net/api"
USER_AGENT = "batlab-musicguard/1.0 (self-hosted music library)"
TIMEOUT = 20
# A search answer carries every match's full lyrics; one lyric is capped by
# clean() and MAX_LINES, and a sidecar on disk by MAX_SIDECAR_BYTES.
MAX_BYTES = 4 * 1024 * 1024
MAX_SIDECAR_BYTES = 256 * 1024
MAX_LINES = 3000
MAX_LINE = 500
SYNCED_LINE = re.compile(r"^\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")
METADATA_LINE = re.compile(r"^\[(?:ar|al|ti|au|length|by|offset|re|ve|#):", re.IGNORECASE)

_last_request = 0.0


def _get(path, params):
    global _last_request
    # One request a second at most: this is someone else's free service.
    wait = 1.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    if len(body) > MAX_BYTES:
        raise ValueError("LRCLIB answer too large")
    return json.loads(body)


def clean(text):
    """Lyrics as plain text: no control characters, bounded size."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = []
    for line in text.split("\n")[:MAX_LINES]:
        line = "".join(c for c in line if c == "\t" or unicodedata.category(c) != "Cc")
        lines.append(line.rstrip()[:MAX_LINE])
    return "\n".join(lines).strip() + "\n"


def synced_ok(text):
    """True when the lines carrying words all start with a timestamp."""
    lines = [line for line in text.splitlines() if line.strip() and not METADATA_LINE.match(line)]
    return bool(lines) and all(SYNCED_LINE.match(line) for line in lines)


def pick(results, seconds):
    """The search result whose length is closest to the track, within 3 s."""
    best, best_gap = None, 3.5
    for result in results or []:
        try:
            gap = abs(float(result.get("duration") or 0) - seconds)
        except (TypeError, ValueError):
            continue
        if gap < best_gap and (result.get("syncedLyrics") or result.get("plainLyrics")
                               or result.get("instrumental")):
            best, best_gap = result, gap
    return best


def lookup(artist, title, album, seconds):
    """("synced"|"plain"|"instrumental"|None, text)."""
    params = {"artist_name": artist, "track_name": title, "duration": int(round(seconds))}
    if album:
        params["album_name"] = album
    found = _get("get", params)
    if not found:
        found = pick(_get("search", {"artist_name": artist, "track_name": title}), seconds)
    if not found:
        return None, ""
    if found.get("instrumental"):
        return "instrumental", ""
    synced = clean(found.get("syncedLyrics") or "")
    if synced.strip() and synced_ok(synced):
        return "synced", synced
    plain = clean(found.get("plainLyrics") or "")
    if plain.strip():
        return "plain", plain
    return None, ""
