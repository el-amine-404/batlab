"""The stores' 30-second previews of a song, to hear whether a file is it.

Spotify's comes from the track's embed page (its API no longer gives one to
new apps); Apple's from the iTunes search API, keyed by title and artist.
Only the search terms and the preview download leave the server.
"""

import json
import os
import re
import tempfile
import urllib.parse

from . import fingerprint, http, titles

MAX_PREVIEW_BYTES = 4 * 1024 * 1024
SPOTIFY_PREVIEW = re.compile(r"https://p\.scdn\.co/mp3-preview/[0-9a-f]+")
APPLE_SEARCH = "https://itunes.apple.com/search"
# Spotify's embed page answers a browser; Apple asks for 20 requests a minute at most.
BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
APPLE_SPACING = 3.0


def spotify(track_id):
    """The preview's URL for a Spotify track ID, or None."""
    page = http.get(f"https://open.spotify.com/embed/track/{urllib.parse.quote(track_id)}", 3_000_000,
                    timeout=20, agent=BROWSER).decode(errors="replace")
    found = SPOTIFY_PREVIEW.search(page)
    return found.group(0) if found else None


def apple(title, artist):
    """The preview's URL of this song on Apple Music (same title, an artist
    in common), or None."""
    query = urllib.parse.urlencode({"term": f"{artist} {title}", "entity": "song", "limit": 10})
    results = json.loads(http.get(f"{APPLE_SEARCH}?{query}", 1024 * 1024, spacing=APPLE_SPACING)).get("results") or []
    for result in results:
        url = result.get("previewUrl") or ""
        if url.startswith("https://audio-ssl.itunes.apple.com/") and titles.agrees(
                result.get("trackName", ""), result.get("artistName", ""), title, artist):
            return url
    return None


def score(path, preview_url):
    """How much the file sounds like the preview: from 0.5 (another song) to
    1.0 (fingerprint.SAME and up: the song)."""
    host = urllib.parse.urlsplit(preview_url).netloc
    data = http.get(preview_url, MAX_PREVIEW_BYTES, spacing=APPLE_SPACING if "apple" in host else 0.0)
    with tempfile.TemporaryDirectory(prefix="batlab-preview-") as workdir:
        preview = os.path.join(workdir, "preview")
        with open(preview, "wb") as out:
            out.write(data)
        return fingerprint.similarity(fingerprint.fingerprint(preview), fingerprint.fingerprint(path))
