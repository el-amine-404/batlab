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

from batlab_common import titles

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


def _get(path, params, attempts=3):
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
        if error.code in (429, 503) and attempts > 1:
            time.sleep(5 * (4 - attempts))  # busy: 5 s, then 10 s
            return _get(path, params, attempts - 1)
        raise
    if len(body) > MAX_BYTES:
        raise ValueError("LRCLIB answer too large")
    return json.loads(body)


# Cyrillic and Greek letters drawn like Latin ones. Inside a Latin word they
# are a typing or copying accident ("ri7tеk" in LRCLIB's KATANA had a
# Cyrillic е): the word reads the same but is not found by a search.
LOOKALIKES = str.maketrans("аеорсухіјѕАВЕКМНОРСТХІЈЅοΑΒΕΗΙΚΜΝΟΡΤΧΥΖ",
                           "aeopcyxijsABEKMHOPCTXIJSoABEHIKMNOPTXYZ")
WORD = re.compile(r"\w+")
# What an AI-synced .lrc starts with (ai-sync on a workstation): its timings
# were not checked by a person, so a human-synced one replaces it.
AI_MARK = "[by:AI-generated"


def latinise(match):
    word = match.group(0)
    if any("a" <= c.lower() <= "z" for c in word):
        return word.translate(LOOKALIKES)
    return word  # a word in Russian or Greek is left as it is


def is_ai(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return AI_MARK in handle.read(4096)
    except OSError:
        return False


def clean(text):
    """Lyrics as plain text: no control characters, no look-alike letters
    inside Latin words, bounded size."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = []
    for line in text.split("\n")[:MAX_LINES]:
        line = "".join(c for c in line if c == "\t" or unicodedata.category(c) != "Cc")
        line = WORD.sub(latinise, line)
        lines.append(line.rstrip()[:MAX_LINE])
    return "\n".join(lines).strip() + "\n"


def synced_ok(text):
    """True when the lines carrying words all start with a timestamp."""
    lines = [line for line in text.splitlines() if line.strip() and not METADATA_LINE.match(line)]
    return bool(lines) and all(SYNCED_LINE.match(line) for line in lines)


def pick(results, seconds, title=None, artist=None):
    """The best entry for the track: same length within 3 s (and, when given,
    the same title and an artist in common), synced lyrics before plain ones,
    then the closest length. LRCLIB often holds a song several times, under
    each way of writing its credit; only one may have timings."""
    best, best_key = None, None
    for result in results or []:
        try:
            gap = abs(float(result.get("duration") or 0) - seconds)
        except (TypeError, ValueError):
            continue
        if gap >= 3.5:
            continue
        if title and not titles.same_title(result.get("trackName") or "", title):
            continue
        if artist and not titles.names(result.get("artistName") or "") & titles.names(artist):
            continue
        synced = clean(result.get("syncedLyrics") or "")
        if synced.strip() and synced_ok(synced):
            rank = 0
        elif (result.get("plainLyrics") or "").strip() or result.get("instrumental"):
            rank = 1
        else:
            continue
        key = (rank, gap)
        if best_key is None or key < best_key:
            best, best_key = result, key
    return best


def rank_synced(result, seconds):
    """True for an entry with usable synced lyrics of about this length."""
    try:
        if abs(float(result.get("duration") or 0) - seconds) >= 3.5:
            return False
    except (TypeError, ValueError):
        return False
    synced = clean(result.get("syncedLyrics") or "")
    return bool(synced.strip()) and synced_ok(synced)


def lookup(artist, title, album, seconds):
    """("synced"|"plain"|"instrumental"|None, text). Every entry with this
    title is considered (an exact match, a search under the full credit, a
    search by title alone, which finds the song under each artist's name),
    and synced lyrics win."""
    params = {"artist_name": artist, "track_name": title, "duration": int(round(seconds))}
    if album:
        params["album_name"] = album
    candidates = []
    exact = _get("get", params)
    if exact:
        candidates.append(exact)
    candidates += _get("search", {"artist_name": artist, "track_name": title}) or []
    if not any(rank_synced(c, seconds) for c in candidates):
        candidates += _get("search", {"track_name": title}) or []
    found = pick(candidates, seconds, title, artist)
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
