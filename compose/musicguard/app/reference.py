"""Is this file the song its tags name? Compared with the real recording.

Shazam answers "which song is this", from its own catalogue, which also holds
re-uploads of hits under other names: it heard 21 Savage's "4L" as
"Thedjnino - Just TEN", an entry with no ISRC and no Apple Music release. So
before a file is called the wrong song, it is compared with Apple Music's
30-second preview of the song its tags name: Chromaprint fingerprints of both
(the technique behind AcoustID), the preview slid along the file, and the
share of matching fingerprint bits at the best offset. The same song scores
about 0.99, different songs about 0.55 (chance is 0.5).

Only the search terms and the preview download leave the server.
"""

import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

import identify

SEARCH = "https://itunes.apple.com/search"
SAME = 0.85  # this or more: the file is the tagged song
OTHER = 0.70  # below this: it is not
MAX_PREVIEW_BYTES = 4 * 1024 * 1024

_last_request = 0.0


def _get(url, limit):
    global _last_request
    wait = 3.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "batlab-musicguard/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(limit + 1)


def preview_url(title, artist):
    """Apple Music's preview of this song (same title, an artist in common), or None."""
    query = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 10})
    results = json.loads(_get(f"{SEARCH}?{query}", 1024 * 1024)).get("results") or []
    for result in results:
        url = result.get("previewUrl") or ""
        if url.startswith("https://audio-ssl.itunes.apple.com/") and identify.agrees(
                identify.Match(result.get("trackName", ""), result.get("artistName", "")), title, artist):
            return url
    return None


def fingerprint(path):
    out = subprocess.run(["fpcalc", "-raw", "-json", "-length", "0", path], stdin=subprocess.DEVNULL,
                         capture_output=True, timeout=300, check=True).stdout
    return json.loads(out)["fingerprint"]


def similarity(short, long):
    """Best share of equal bits with short slid along long."""
    if not short or not long:
        return 0.0
    if len(short) > len(long):
        short, long = long, short
    best = 0
    for offset in range(len(long) - len(short) + 1):
        same = sum(32 - ((a ^ b) & 0xFFFFFFFF).bit_count() for a, b in zip(short, long[offset:]))
        best = max(best, same)
    return best / (32 * len(short))


def compare(path, title, artist):
    """How much the file sounds like Apple's preview of title by artist:
    a score from 0.5 (unrelated) to 1.0, or None when Apple has no preview."""
    url = preview_url(title, artist)
    if not url:
        return None
    data = _get(url, MAX_PREVIEW_BYTES)
    if len(data) > MAX_PREVIEW_BYTES:
        return None
    with tempfile.TemporaryDirectory(prefix="musicguard-preview-") as workdir:
        preview = os.path.join(workdir, "preview.m4a")
        with open(preview, "wb") as out:
            out.write(data)
        return similarity(fingerprint(preview), fingerprint(path))
