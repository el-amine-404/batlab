"""Which side of "X - Y" is the artist, asked of MusicBrainz.

Only for link downloads whose title could be either "Artist - Song" or
"Song - Artist" and whose channel name matches neither side. MusicBrainz asks
for at most one request a second and a User-Agent naming the client.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://musicbrainz.org/ws/2/recording"
USER_AGENT = "batlab-musicguard/1.0 (self-hosted music library)"
MIN_SCORE = 90

_last_request = 0.0


def _best_score(artist, title, attempts=3):
    global _last_request
    # Their limit is one a second on average; 2 s keeps clear of the 503s
    # that answer anything faster.
    wait = 2.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    try:
        return _query(artist, title)
    except urllib.error.HTTPError as error:
        if error.code != 503 or attempts <= 1:
            raise
        time.sleep(5)
        return _best_score(artist, title, attempts - 1)


def _query(artist, title):
    def quoted(text):
        return '"' + text.replace("\\", " ").replace('"', " ") + '"'
    query = f"recording:{quoted(title)} AND artist:{quoted(artist)}"
    url = f"{API}?{urllib.parse.urlencode({'query': query, 'fmt': 'json', 'limit': 1})}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        found = json.loads(response.read(512 * 1024))
    recordings = found.get("recordings") or []
    return int(recordings[0].get("score", 0)) if recordings else 0


def artist_side(left, right):
    """"left", "right", or None when MusicBrainz knows neither order (or is
    unreachable: a guess is worse than leaving the title as it was)."""
    try:
        as_left = _best_score(left, right)
        as_right = _best_score(right, left)
    except Exception:
        return None
    if max(as_left, as_right) < MIN_SCORE or as_left == as_right:
        return None
    return "left" if as_left > as_right else "right"
