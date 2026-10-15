"""A pasted link's song, handed to Lidarr for the real thing.

A song from YouTube or Spotify is YouTube's audio (Opus, about 130-160 kb/s).
For every song in the extras library this finds the album it comes from on
MusicBrainz (the full album where there is one, else the EP, else the single),
has Lidarr monitor and search that album, and removes the YouTube copy once
Lidarr's file for the same recording is in the library. Lidarr then keeps
upgrading it to lossless like any other album.

The same goes for a single in Lidarr's own library: a song Lidarr filed as a
one-track release ("a lot", "Glimpse of Us") gets the album it is on, and the
single is removed through Lidarr (to its recycle bin) and unmonitored once
the album's copy is in the library and verified, so the song is there once.

A song MusicBrainz does not know (much Moroccan music is only on streaming
services) cannot be fetched by Lidarr, whose catalogue is MusicBrainz: it keeps
its YouTube copy and is looked up again a week later.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from batlab_common import titles

MUSICBRAINZ = "https://musicbrainz.org/ws/2/recording"
USER_AGENT = "batlab-musicguard/1.0 (self-hosted music library)"
MIN_SCORE = 90
# Release-group kinds, best first. Anything with a secondary type (live,
# compilation, remix, soundtrack, DJ-mix) is not the song's own release.
KIND_ORDER = {"Album": 0, "EP": 1, "Single": 2}

_last_request = 0.0


def _musicbrainz(query, attempts=3):
    global _last_request
    wait = 2.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    url = MUSICBRAINZ + "?" + urllib.parse.urlencode({"query": query, "fmt": "json", "limit": 10})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read(4 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        if error.code == 503 and attempts > 1:
            time.sleep(5)
            return _musicbrainz(query, attempts - 1)
        raise


def _quoted(text):
    return '"' + text.replace("\\", " ").replace('"', " ") + '"'


def recording(recording_id, attempts=3):
    """A MusicBrainz recording with its releases and their release groups."""
    global _last_request
    wait = 2.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    url = f"{MUSICBRAINZ}/{urllib.parse.quote(recording_id)}?" + urllib.parse.urlencode(
        {"inc": "releases release-groups", "fmt": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read(4 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        if error.code == 503 and attempts > 1:
            time.sleep(5)
            return recording(recording_id, attempts - 1)
        raise


def recordings_for(title, artist, isrc=""):
    """MusicBrainz recordings of this song: by ISRC when the file has one,
    else by title and artist, keeping only confident, matching answers."""
    found = []
    if isrc:
        found = _musicbrainz(f"isrc:{_quoted(isrc)}").get("recordings") or []
    if not found:
        found = _musicbrainz(f"recording:{_quoted(title)} AND artist:{_quoted(artist)}").get("recordings") or []
        found = [r for r in found if int(r.get("score", 0)) >= MIN_SCORE]
    wanted = titles.names(artist)
    return [r for r in found
            if titles.same_title(r.get("title", ""), title) and titles.same_version(r.get("title", ""), title)
            and wanted & titles.names(" & ".join(c.get("name", "") for c in r.get("artist-credit") or []))]


def best_release_group(recordings):
    """(release-group id, title, kind) of the song's own album, EP or single,
    the earliest of the best kind; None when it is on none of them."""
    best = None
    for recording in recordings:
        for release in recording.get("releases") or []:
            if release.get("status") not in (None, "Official"):
                continue
            group = release.get("release-group") or {}
            kind = group.get("primary-type")
            if kind not in KIND_ORDER or group.get("secondary-types") or not group.get("id"):
                continue
            key = (KIND_ORDER[kind], release.get("date") or "9999")
            if best is None or key < best[0]:
                best = (key, group["id"], group.get("title") or release.get("title", ""), kind)
    return best[1:] if best else None


class Lidarr:
    def __init__(self, url, key):
        self.url = url.rstrip("/") + "/api/v1/"
        self.key = key

    def call(self, method, path, body=None, **query):
        url = self.url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method,
                                         headers={"X-Api-Key": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read(32 * 1024 * 1024)
        return json.loads(text) if text else None

    def album(self, group_id):
        """Lidarr's album for a release group, or None when its artist is not in Lidarr."""
        found = self.call("GET", "album", foreignAlbumId=group_id) or []
        return found[0] if found else None

    def add_album(self, group_id):
        """Add the album, and its artist if new, monitored and searched for.
        The artist gets no other album monitored: only what was asked for."""
        found = self.call("GET", "album/lookup", term=f"lidarr:{group_id}") or []
        if not found:
            return None
        album = found[0]
        root = (self.call("GET", "rootfolder") or [])[0]
        album["artist"].update({
            "qualityProfileId": root["defaultQualityProfileId"],
            "metadataProfileId": root["defaultMetadataProfileId"],
            "rootFolderPath": root["path"],
            "monitored": True,
            "monitorNewItems": "none",
            # "none" alone unmonitors every album once the artist is added,
            # the asked one too (Lidarr's post-add actions, 2026-10-15).
            "addOptions": {"monitor": "none", "albumsToMonitor": [group_id], "searchForMissingAlbums": False},
        })
        album["monitored"] = True
        album["addOptions"] = {"searchForNewAlbum": True}
        return self.call("POST", "album", album)

    def want(self, album):
        """Monitor an album already in Lidarr, and its artist, and search for
        it. True when it had to: it was not wanted."""
        changed = False
        artist = self.call("GET", f"artist/{album['artistId']}") if album.get("artistId") else None
        if artist and not artist.get("monitored"):
            artist["monitored"] = True
            self.call("PUT", f"artist/{artist['id']}", artist)
            changed = True
        if not album.get("monitored"):
            self.call("PUT", "album/monitor", {"albumIds": [album["id"]], "monitored": True})
            changed = True
        if changed:
            self.call("POST", "command", {"name": "AlbumSearch", "albumIds": [album["id"]]})
        return changed

    def still_wanted(self, album_id):
        """want() for an album asked for earlier, which Lidarr may have
        unmonitored since. True when it had to."""
        return self.want(self.call("GET", f"album/{album_id}"))

    def remove_track_file(self, group_id, path):
        """Delete the file at path from the album of that release group (Lidarr
        moves it to its recycle bin), and unmonitor the album once it has no
        file left, so Lidarr does not fetch it again."""
        album = self.album(group_id)
        if not album:
            return False
        files = self.call("GET", "trackfile", albumId=album["id"]) or []
        removed = False
        for item in files:
            if item.get("path") == path:
                self.call("DELETE", f"trackfile/{item['id']}")
                removed = True
        if removed and len(files) == 1:
            self.call("PUT", "album/monitor", {"albumIds": [album["id"]], "monitored": False})
        return removed

    def track_file(self, album_id, recording_ids, title):
        """Path of Lidarr's file for this song on that album, or None. Never
        the original for a remix or a live take (titles.version)."""
        for track in self.call("GET", "track", albumId=album_id) or []:
            if not track.get("hasFile"):
                continue
            named = track.get("title", "")
            if track.get("foreignRecordingId") in recording_ids or (
                    titles.same_title(named, title) and titles.same_version(named, title)):
                return (self.call("GET", f"trackfile/{track['trackFileId']}") or {}).get("path")
        return None
