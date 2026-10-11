"""Full artist credits from MusicBrainz, for files Lidarr tagged.

Lidarr writes MusicBrainz's data into every file it imports, except that the
artist and album artist hold only Lidarr's one artist name: "KATANA" by
"Manal & Stormy" is written as "Manal". This puts back the credit MusicBrainz
has for that release and track, joining phrases included, and leaves every
other tag as Lidarr wrote it. The edit is in place (no rebuild), so the file
keeps its inode and its hardlink to the seeding torrent.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import mutagen

API = "https://musicbrainz.org/ws/2/release/"
USER_AGENT = "batlab-musicguard/1.0 (self-hosted music library)"

_last_request = 0.0


def _fetch(release_id, attempts=3):
    global _last_request
    wait = 2.0 - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    url = API + urllib.parse.quote(release_id) + "?" + urllib.parse.urlencode(
        {"inc": "recordings artist-credits", "fmt": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read(4 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        if error.code == 503 and attempts > 1:
            time.sleep(5)
            return _fetch(release_id, attempts - 1)
        raise


def credit_text(credit):
    return "".join(part.get("name", "") + part.get("joinphrase", "") for part in credit or []).strip()


def credit_names(credit):
    return [part.get("name", "") for part in credit or [] if part.get("name")]


def release_credits(release):
    """{"album": (text, names), "tracks": {track_id or recording_id: (text, names)}}."""
    tracks = {}
    for medium in release.get("media") or []:
        for track in medium.get("tracks") or []:
            credit = track.get("artist-credit") or (track.get("recording") or {}).get("artist-credit")
            entry = (credit_text(credit), credit_names(credit))
            if track.get("id"):
                tracks[track["id"]] = entry
            if (track.get("recording") or {}).get("id"):
                tracks.setdefault(track["recording"]["id"], entry)
    album = release.get("artist-credit")
    return {"album": (credit_text(album), credit_names(album)), "tracks": tracks}


class Credits:
    def __init__(self, cache):
        # cache: a dict kept in musicguard's state, release id -> credits.
        self.cache = cache

    def for_release(self, release_id):
        if release_id not in self.cache:
            self.cache[release_id] = release_credits(_fetch(release_id))
        return self.cache[release_id]

    def wanted(self, found):
        """(artist text, artists list, album artist text) for a file's tags,
        or None when its IDs do not lead to a credit."""
        release_id = (found.get("musicbrainz_albumid") or [""])[0]
        if not release_id:
            return None
        credits = self.for_release(release_id)
        track = None
        for key in ("musicbrainz_releasetrackid", "musicbrainz_trackid"):
            track_id = (found.get(key) or [""])[0]
            if track_id in credits["tracks"]:
                track = credits["tracks"][track_id]
                break
        album_text = credits["album"][0]
        if not track or not track[0] or not album_text:
            return None
        return track[0], track[1], album_text


def apply(path, artist, artists, album_artist):
    """Write the three credit fields in place. Returns the names changed."""
    audio = mutagen.File(path, easy=True)
    if audio is None or audio.tags is None:
        return []
    changed = []
    for key, value in (("artist", [artist]), ("albumartist", [album_artist]), ("artists", artists)):
        try:
            current = list(audio.tags.get(key, []))
        except KeyError:
            current = []
        if value and current != value:
            try:
                audio.tags[key] = value
                changed.append(key)
            except (KeyError, ValueError):
                pass
    if changed:
        audio.save()
    return changed
