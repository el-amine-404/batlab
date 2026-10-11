"""Album data for a song from a link, from Apple Music's public lookup API.

Shazam's answer names the song's Apple Music track, and Apple's catalogue has
what MusicBrainz often lacks (Moroccan releases above all): the real album
(without the " - EP" / " - Single" Apple appends), the track number, the
release date, the genre and the cover. No account or key; only the track ID
is sent.
"""

import json
import re
import time
import urllib.parse
import urllib.request

API = "https://itunes.apple.com/lookup"
COVER_SIZE = "1200x1200bb.jpg"
MAX_COVER_BYTES = 8 * 1024 * 1024
KIND = re.compile(r"\s+-\s+(EP|Single)$")

_last_request = 0.0


def _get(url, limit):
    global _last_request
    # Apple allows about 20 lookups a minute.
    wait = 3.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "batlab-musicguard/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(limit + 1)


def _lookup(item_id):
    answer = json.loads(_get(API + "?" + urllib.parse.urlencode({"id": item_id}), 1024 * 1024))
    results = answer.get("results") or []
    return results[0] if results else None


class Release:
    def __init__(self, track, collection):
        name = (track.get("collectionName") or "").strip()
        kind = KIND.search(name)
        self.album = KIND.sub("", name)
        self.kind = kind.group(1).lower() if kind else "album"
        self.title = (track.get("trackName") or "").strip()
        self.artist = (track.get("artistName") or "").strip()
        self.album_artist = ((collection or {}).get("artistName") or self.artist).strip()
        self.track = track.get("trackNumber")
        self.tracks = track.get("trackCount")
        self.disc = track.get("discNumber")
        self.discs = track.get("discCount")
        self.date = (track.get("releaseDate") or "")[:10]
        self.genre = (track.get("primaryGenreName") or "").strip()
        artwork = track.get("artworkUrl100") or ""
        self.cover_url = re.sub(r"/[^/]+$", "/" + COVER_SIZE, artwork) if artwork.startswith("https://") else ""

    def __repr__(self):
        return f"{self.album_artist} - {self.album} ({self.kind}, {self.date[:4]}) #{self.track}"

    def tags(self):
        """(key, value) pairs for tags.collect, which checks every value."""
        pairs = [("album", self.album), ("albumartist", self.album_artist), ("date", self.date),
                 ("genre", self.genre), ("releasetype", self.kind)]
        if self.track:
            pairs.append(("tracknumber", f"{self.track}/{self.tracks}" if self.tracks else str(self.track)))
        if self.disc:
            pairs.append(("discnumber", f"{self.disc}/{self.discs}" if self.discs else str(self.disc)))
        return [(k, v) for k, v in pairs if v]

    def cover(self):
        """The cover's bytes (decoded and re-encoded by the caller), or None."""
        if not self.cover_url.startswith("https://is") or ".mzstatic.com/" not in self.cover_url:
            return None
        data = _get(self.cover_url, MAX_COVER_BYTES)
        return data if len(data) <= MAX_COVER_BYTES else None


def release(track_id):
    """The Release of an Apple Music track ID, or None."""
    track = _lookup(track_id)
    if not track or track.get("wrapperType") != "track":
        return None
    collection = _lookup(track["collectionId"]) if track.get("collectionId") else None
    return Release(track, collection)
